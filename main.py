#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""
  i3-quickterm.py
  ===============

  Description:           A small drop-down terminal for i3wm.
  Author:                lbonn <github.com/lbonn>
  Creation Date:         2016-12-26
  Modification Date:     2019-04-13

"""

import copy
import fcntl
import json
import logging
import os
import shlex
import subprocess
import sys
from typing import Tuple, Union

from contextlib import contextmanager, suppress
from pathlib import Path

import click
import i3ipc


DEFAULT_CONF = {
    'menu': "rofi -dmenu -p 'quickterm: ' -no-custom -auto-select",
    'term': 'urxvt',
    'history': '{$HOME}/.cache/i3/i3-quickterm.order',
    'ratio': 0.25,
    'pos': 'top',
    'shells': {
        'haskell': 'ghci',
        'js': 'node',
        'python': 'ipython3 --no-banner',
        'shell': '{$SHELL}'
    }
}


MARK_QT_PATTERN = 'quickterm_.*'
MARK_QT = 'quickterm_{}'


def TERM(executable: str,
         execopt: str = '-e',
         execfmt: str = 'expanded',
         titleopt: Union[str, None] = '-T',
         classopt: Union[str, None] = None) -> str:
    """ Helper to declare a terminal in the hardcoded list """
    if execfmt not in ('expanded', 'string'):
        raise RuntimeError('Invalid execfmt')

    if titleopt is not None:
        executable += ' ' + titleopt + ' {title}'

    if classopt is not None:
        executable += ' ' + classopt + ' {class_name}'

    return executable + ' {} {{{}}}'.format(execopt, execfmt)


TERMS = {
    'alacritty': TERM('alacritty', titleopt='-t'),
    'kitty': TERM('kitty', titleopt='-T', classopt='--class'),
    'gnome-terminal': TERM('gnome-terminal', execopt='--', titleopt=None),
    'roxterm': TERM('roxterm'),
    'st': TERM('st'),
    'termite': TERM('termite', execfmt='string', titleopt='-t'),
    'urxvt': TERM('urxvt'),
    'urxvtc': TERM('urxvtc'),
    'xfce4-terminal': TERM('xfce4-terminal', execfmt='string'),
    'xterm': TERM('xterm'),
}


def conf_path() -> str:
    """ Returns the path to the configuration file. """
    home_dir = os.environ['HOME']
    xdg_dir = os.environ.get('XDG_CONFIG_DIR', '{}/.config'.format(home_dir))

    return xdg_dir + '/i3/i3-quickterm.json'


def read_conf(fn: str) -> dict:
    """ Reads the configuration file."""
    try:
        with open(fn, 'r') as f:
            c = json.load(f)

        return c

    except Exception as e:
        logging.error('invalid config file: {}'.format(e))
        return {}


@contextmanager
def get_history_file(conf: dict) -> object:
    if conf['history'] is None:
        yield None
        return

    p = Path(expand_command(conf['history'])[0])

    os.makedirs(str(p.parent), exist_ok=True)

    f = open(str(p), 'a+')
    fcntl.lockf(f, fcntl.LOCK_EX)

    try:
        f.seek(0)
        yield f

    finally:
        fcntl.lockf(f, fcntl.LOCK_UN)
        f.close()


def expand_command(cmd: str, **rplc_map) -> str:
    logging.debug('expand_cmd: "%s" (%s)' % (cmd, rplc_map))
    d = {'$' + k: v for k, v in os.environ.items()}
    d.update(rplc_map)

    return shlex.split(cmd.format(**d))


def i3cmd(conn: i3ipc.Connection, cmd: str) -> None:
    """ Wrapper for conn.command that logs commands prior to running. """
    logging.debug('i3 cmd: %s' % cmd)
    conn.command(cmd)


def move_back(conn: i3ipc.Connection, selector: str) -> None:
    i3cmd(conn, '{} floating enable, move scratchpad'.format(selector))


def pop_it(conn: i3ipc.Connection,
           mark_name: str,
           pos: str = 'top',
           ratio: float = 0.25) -> None:
    assert pos in ('top', 'bottom')
    ws, _ = get_current_workspace(conn)
    wx, wy = ws['rect']['x'], ws['rect']['y']
    wwidth, wheight = ws['rect']['width'], ws['rect']['height']

    width = wwidth
    height = int(wheight*ratio)
    posx = wx

    if pos == 'bottom':
        margin = 6
        posy = wy + wheight - height - margin

    else:  # pos == 'top'
        posy = wy

    i3cmd(conn, (
            '[con_mark={mark}],'
            'resize set {width} px {height} px,'
            'move absolute position {posx}px {posy}px,'
            'move scratchpad,'
            'scratchpad show'
        ).format(
            mark=mark_name,
            posx=posx,
            posy=posy,
            width=width,
            height=height
        )
    )


def get_current_workspace(
        conn: i3ipc.Connection) -> Tuple[i3ipc.WorkspaceReply, object]:
    """ Get the focused workspace.

        Returns
        -------
        A tuple in form (workspace, con), with the focused workspace and
        the container object for the focused workspace, respectively.
    """
    ws = [w for w in conn.get_workspaces() if w['focused']][0]
    tree = conn.get_tree()
    ws_tree = [c for c in tree.descendents()
               if c.type == 'workspace' and c.name == ws['name']][0]

    return ws, ws_tree


def toggle_quickterm_select(conf: dict) -> None:
    """ Hide a quickterm visible on current workspace or prompt
        the user for a shell type.
    """
    conn = i3ipc.Connection()
    ws, ws_tree = get_current_workspace(conn)

    # is there a quickterm opened in the current workspace?
    qt = ws_tree.find_marked(MARK_QT_PATTERN)
    if qt:
        qt = qt[0]
        move_back(conn, '[con_id={}]'.format(qt.id))
        return

    with get_history_file(conf) as hist:
        # compute the list from conf + (maybe) history
        hist_list = None

        if hist is not None:
            with suppress(Exception):
                hist_list = json.load(hist)

                # invalidate if different set from the configured shells
                if set(hist_list) != set(conf['shells'].keys()):
                    hist_list = None

        shells = hist_list or sorted(conf['shells'].keys())

        proc = subprocess.Popen(expand_command(conf['menu']),
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE)

        for r in shells:
            proc.stdin.write((r + '\n').encode())

        stdout, _ = proc.communicate()
        shell = stdout.decode().strip()

        if shell not in conf['shells']:
            return

        if hist is not None:
            # put the selected shell on top
            shells = [shell] + [s for s in shells if s != shell]
            hist.truncate(0)
            json.dump(shells, hist)

    toggle_quickterm(conf, shell)


def quoted(s: str, char: str = "'") -> str:
    return char + s + char


def term_title(shell: str) -> str:
    """ Returns a title for the given shell. """
    return '{} - i3-quickterm'.format(shell)


def toggle_quickterm(conf: dict, shell: str) -> None:
    """ Toggles an existing drop-down terminal for the given shell, or starts
        one if it is not running.
    """
    conn = i3ipc.Connection()
    tree = conn.get_tree()
    shell_mark = MARK_QT.format(shell)
    qt = tree.find_marked(shell_mark)

    # does it exist already?
    if len(qt) == 0:
        logging.debug('no existing terminal for mark %s' % shell_mark)
        term = TERMS.get(conf['term'], conf['term'])
        qt_cmd = expand_command(conf['shells'][shell])[0]
        title = term_title(shell)
        classname = conf['term'] + '-quickterm'
        term_cmd = ' '.join(expand_command(
            quoted(term),
            title=quoted(title, '"'),
            class_name=quoted(classname, '"'),
            expanded=qt_cmd,
            string=quoted(conf['shells'][shell])
        ))

        done = False

        def on_window_focus(conn: i3ipc.Connection, event: i3ipc.WindowEvent):
            nonlocal done
            window = event.container
            logging.debug('focused window: "%s"' % window.window_instance)

            # FIXME: instance isn't necessarily what we expect..
            if not done and (window.window_instance == classname or
                             window.window_instance == conf['term']):
                done = True
                shell_mark = MARK_QT.format(shell)
                i3cmd(conn, 'mark {}'.format(shell_mark))
                move_back(conn, '[con_mark={}]'.format(shell_mark))
                pop_it(conn, shell_mark, conf['pos'], conf['ratio'])

        conn.on('window::focus', on_window_focus)
        i3cmd(conn, 'exec %s' % term_cmd)
        conn.main(timeout=2)

    else:
        qt = qt[0]
        ws, ws_tree = get_current_workspace(conn)
        move_back(conn, '[con_id={}]'.format(qt.id))

        if qt.workspace().name != ws.name:
            pop_it(conn, shell_mark, conf['pos'], conf['ratio'])


@click.command()
@click.option(
    '-v', '--verbose',
    count=True,
    default=0,
    help='Controls the verbosity level.',
    type=int,
)
@click.argument(
    'shell',
    default=None,
    nargs=1,
    required=False,
    type=str,
)
def main(verbose: int, shell: str) -> int:
    """ A small drop-down terminal for i3wm. """
    # Initialise logger
    logging.basicConfig(
        format=(
            '[%(asctime)s.%(msecs)03d %(levelname)s]'
            ' %(name)s.%(funcName)s:%(lineno)s %(message)s'
        ),
        datefmt='%Y-%m-%d %H:%M:%S',
        level=(30 - verbose * 10),
    )

    # Read configuration
    conf = copy.deepcopy(DEFAULT_CONF)
    conf.update(read_conf(conf_path()))

    if shell is None:
        toggle_quickterm_select(conf)
        return 0

    if shell not in conf['shells']:
        logging.error(
            'Unknown shell "%s" (available shells: %s)',
            shell,
            ', '.join(list(conf['shells'].keys()))
        )
        return 1

    toggle_quickterm(conf, shell)
    return 0


if __name__ == '__main__':
    sys.exit(main.main(sys.argv[1:], standalone_mode=False))

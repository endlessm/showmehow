# /showmehow/remindmehow.py
#
# Copyright (c) 2016-2017 Endless Mobile Inc.
#
# remindmehow - entrypoint
"""Entry point for showmehow."""

import argparse
import os
import sys
import textwrap
import time

import gi

gi.require_version("Showmehow", "1.0")
gi.require_version("Gio", "2.0")

from gi.repository import (Gio, Showmehow)

from showmehow import (create_service,
                       practice_task,
                       print_lines_slowly,
                       show_tasks,
                       ReloadMonitor)


def main(argv=None):
    """Entry point for remindmehow."""
    parser = argparse.ArgumentParser('remindmehow - Remind me how to do things')
    parser.add_argument('task',
                        nargs='?',
                        metavar='TASK',
                        help='TASK to perform',
                        type=str)

    arguments = parser.parse_args(argv or sys.argv[1:])

    service = create_service()
    known_tasks = service.call_get_known_spells_sync("console")

    if not len(known_tasks):
        print_lines_slowly("You haven't completed any tasks yet. "
                           "Run showmehow to complete some")
        return

    try:
        task = [t for t in known_tasks if t[0] == arguments.task][0]
    except IndexError:
        if arguments.task:
            print_lines_slowly("You haven't completed tasks {}".format(arguments.task))
        else:
            print_lines_slowly("You've done the following tasks:")
        return show_tasks(known_tasks)

    with ReloadMonitor(service) as monitor:
        return practice_task(service, monitor, *task)


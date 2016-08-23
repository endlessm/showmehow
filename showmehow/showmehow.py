# /showmehow/showmehow.py
#
# showmehow - entrypoint
#
# Copyright (c) 2016 Endless Mobile Inc.
# All rights reserved.
"""Entry point for showmehow."""

import argparse
import os
import sys
import textwrap
import threading
import time

import gi

gi.require_version("Showmehow", "1.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gio", "2.0")

from gi.repository import (GLib, Gio, Showmehow)

try:
    input = raw_input
except NameError:
    pass


_PAUSECHARS = ".?!:"


class ReloadMonitor(object):
    """Monitor a ShowmehowService to see if the content was reloaded."""

    def __init__(self, service):
        """Initialise with service and spawn a thread

        This thread will monitor whether the content behind the service
        has been reloaded. If so, it sets the reloaded property
        to true. This can be read by other consumers in the main
        thread to determine what to do.
        """
        super(ReloadMonitor, self).__init__()

        self.reloaded = False
        self._service = service

        self._thread = None
        self._loop = None

    def start(self):
        """Start the monitoring thread."""
        if not self._thread:
            self._thread = threading.Thread(target=self.monitor_thread)
            self._thread.start()

    def __enter__(self):
        """Use as a context manager. Start the monitor."""
        self.start()
        return self

    def __exit__(self, exc_type, value, traceback):
        """Close down the monitor."""
        self.quit()

    def quit(self):
        """Stop monitoring for reloads and shut down."""
        if not self._thread or not self._loop:
            return

        self._loop.quit()
        self._thread.join()

        self._thread = None
        self._loop = None

    def monitor_thread(self):
        """Run the GLib main loop and monitor for changes."""
        # We might not have an active service. In that case,
        # just return immediately as there is nothing to
        # monitor.
        if self._service:
            self._service.connect("lessons-changed", self.on_lessons_changed)
            self._loop = GLib.MainLoop()
            self._loop.run()

    def on_lessons_changed(self, proxy):
        """Set the reloaded property to true on this instance."""
        self.reloaded = True


def print_lines_slowly(text, newline=True):
    """Print each character in the line to the standard output."""
    if os.environ.get("NONINTERACTIVE", None):
        print(text)
        return

    text = text + " "
    for ind in range(0, len(text)):
        sys.stdout.write(text[ind])
        sys.stdout.flush()
        time.sleep(0.5 if text[ind] in _PAUSECHARS and
                   text[ind + 1] == " " else 0.02)

    if newline:
        sys.stdout.write("\n")


def print_message_slowly_and_wait(message, wait_time=2):
    """Print message slowly and wait a few seconds, printing dots."""
    print_lines_slowly(message, newline=False)
    for i in range(0, wait_time):
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(1)

    print("")

def practice_lesson_in_task(service, monitor, task_name, lesson_index):
    """Practice a particular lesson for this task."""
    if service:
        (task_desc,
         success_text,
         fail_text) = service.call_get_task_description_sync(task_name, lesson_index)
    else:
        assert os.environ.get("NONINTERACTIVE", False)
        # XXX: Copying these in is not ideal, but we are not able to
        # connect to the service again from within this process if
        # we are non-interactive.
        task_desc = "'showmehow' is a command that you can type, just like any other command. Try typing it and see what happens."
        success_text = "That's right! Though now you need to tell showmehow what task you want to try. This is called an 'argument'. Try giving showmehow an argument so that it knows what to do. Want to know what argument to give it? There's only one, and it just told you what it was."
        fail_text = "Nope, that wasn't what I thought would happen! Try typing just 'showmehow' and hit 'enter'. No more, no less (though surrounding spaces are okay)."

    # If we're non-interactive, assume that the lesson passed. Note that
    # in this case, service will be None, so we need to ensure that
    # we don't call any methods on it.
    result = os.environ.get("NONINTERACTIVE", False)
    n_failed = 0

    print_lines_slowly("\n".join(textwrap.wrap(task_desc)))

    while not result:
        if n_failed > 0:
            time.sleep(0.5)
            print_lines_slowly(fail_text)

        code = input("$ ")

        # Now, check just before submission if the lessons changed
        # from underneath us. We're a blocking application, so this
        # is the best place to put this check. If the lessons did
        # change, get out and notify the user instead of crashing.
        if monitor.reloaded:
            print("Service indicated that lessons were reloaded, "
                  "bailing out now.")
            return False

        (wait_message,
         printable_output,
         result) = service.call_attempt_lesson_remote_sync(task_name,
                                                           lesson_index,
                                                           code)
        print_message_slowly_and_wait(wait_message)
        print("\n".join(textwrap.wrap(printable_output,
                                      initial_indent="> ",
                                      subsequent_indent="> ")))

        # This will always be incremented, but it doesn't matter
        # since it isn't checked anyway if result is True.
        n_failed += 1

    print_lines_slowly("\n".join(textwrap.wrap(success_text)))
    print("")

    return True


def practice_task(service, monitor, task, _, num_lessons, done_text):
    """Practice the task named :task:"""
    wait_time = 2

    for lesson_index in range(0, num_lessons):
        if not practice_lesson_in_task(service, monitor, task, lesson_index):
            break

    print("---")
    print_lines_slowly("\n".join(textwrap.wrap(done_text)))


def show_tasks(tasks):
    """Show tasks that can be done in the terminal."""
    for task in tasks:
        print("[{task[0]}] - {task[1]}".format(task=task))


def create_service():
    """Create a ShowmehowService."""
    service = Showmehow.ServiceProxy.new_for_bus_sync(Gio.BusType.SESSION,
                                                      0,
                                                      "com.endlessm.Showmehow.Service",
                                                      "/com/endlessm/Showmehow/Service")
    # Display any warnings that came through from the service.
    for warning in service.call_get_warnings_sync():
        print("Service warning: " + warning[0])

    return service


def main(argv=None):
    """Entry point. Parse arguments and start the application."""
    parser = argparse.ArgumentParser('showmehow - Show me how to do things')
    parser.add_argument('task',
                        nargs='?',
                        metavar='TASK',
                        help='TASK to perform',
                        type=str)

    arguments = parser.parse_args(argv or sys.argv[1:])

    if os.environ.get("NONINTERACTIVE"):
        service = None
        unlocked_tasks = [("showmehow", "Show me how to do things...", 2, "Done")]
    else:
        service = create_service()
        unlocked_tasks = service.call_get_unlocked_lessons_sync("console")

    try:
        task = [t for t in unlocked_tasks if t[0] == arguments.task][0]
    except IndexError:
        if arguments.task:
            print_lines_slowly("I don't know how to do task {}".format(arguments.task))
        else:
            print_lines_slowly("Hey, how are you? I can tell you about the following tasks:")
        return show_tasks(unlocked_tasks)

    with ReloadMonitor(service) as monitor:
        return practice_task(service, monitor, *task)


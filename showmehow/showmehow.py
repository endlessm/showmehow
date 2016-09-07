# /showmehow/showmehow.py
#
# showmehow - entrypoint
#
# Copyright (c) 2016 Endless Mobile Inc.
# All rights reserved.
"""Entry point for showmehow."""

import argparse
import json
import os
import sys
import textwrap
import threading
import time

from collections import OrderedDict

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


def show_wrapped_response(value):
    """Print wrapped text, quickly."""
    # HACK: textwrap.wrap seems to do a bad job of actually
    # wrapping and splitting on newlines. Avoid it if possible
    # when we already have lines that are less than 70 chars
    if (all([len(l) < 70 for l in value.splitlines()])):
        print("\n".join([
            "> " + l for l in value.splitlines()
        ]))
    else:
        print("\n".join(textwrap.wrap(value,
                                      replace_whitespace=False,
                                      initial_indent="> ",
                                      subsequent_indent="> ")))
class WaitTextFunctor(object):
    """Stateful function to print text slowly and wait."""
    def __init__(self):
        """Initialise."""
        super(WaitTextFunctor, self).__init__()
        self._wait_time = 3

    def __call__(self, text):
        """Print text slowly and wait.

        The wait time will decrease every time this method is called.
        """
        self._wait_time = max(self._wait_time - 1, 1)
        print_message_slowly_and_wait(text, self._wait_time)


def show_response_scrolled(value):
    """Print scrolled text."""
    print_lines_slowly("\n".join(textwrap.wrap(value)))


_RESPONSE_ACTIONS = {
    "scrolled": show_response_scrolled,
    "scroll_wait": WaitTextFunctor(),
    "wrapped": show_wrapped_response
}


def show_response(response):
    """Take a response and show it as appropriate."""
    try:
        _RESPONSE_ACTIONS[response["type"]](response["value"])
    except KeyError:
        raise RuntimeError("Don't know how to handle response type " +
                           response["type"])


def handle_input_choice(choices):
    """Given some choices, allow the user to select a choice."""
    choices = OrderedDict(choices)
    selected_index = len(choices)

    while not selected_index < len(choices):
        for index, desc in enumerate(choices.values()):
            print("({}) {}".format(index + 1, desc["text"]))

        # Show the choices and allow the user to pick on as an index
        try:
            selected_index = int(input("Choice: ")) - 1
        except ValueError:
            selected_index = len(choices)

    return list(choices.keys())[selected_index]


def handle_input_text(prompt):
    """Handle free text input, closure."""
    def inner(*args):
        """Get prompt"""
        del args

        return input(prompt)

    return inner


def handle_input_external_events(*args):
    """Handle external events input."""
    del args

    return "satisfied"

_INPUT_ACTIONS = {
    "choice": handle_input_choice,
    "text": handle_input_text(""),
    "console": handle_input_text("$ "),
    "external_events": handle_input_external_events
}


def handle_input(input_desc):
    """Given some input type, handle the input."""
    try:
        return _INPUT_ACTIONS[input_desc["type"]](input_desc["settings"])
    except KeyError:
        raise RuntimeError("Don't know how to handle input type " +
                           input_desc["type"])


def practice_lesson_in_task(service, monitor, task_name, lesson_id):
    """Practice a particular lesson for this task."""
    if service:
        (task_desc,
         input_desc) = service.call_get_task_description_sync(task_name,
                                                              lesson_id)
    else:
        assert os.environ.get("NONINTERACTIVE", False)
        # XXX: Copying these in is not ideal, but we are not able to
        # connect to the service again from within this process if
        # we are non-interactive.
        #
        # If we're non-interactive, assume that the lesson passed. Note that
        # in this case, service will be None, so we need to ensure that
        # we don't call any methods on it.
        task_desc = "'showmehow' is a command that you can type, just like any other command. Try typing it and see what happens."
        success_text = "That's right! Though now you need to tell showmehow what task you want to try. This is called an 'argument'. Try giving showmehow an argument so that it knows what to do. Want to know what argument to give it? There's only one, and it just told you what it was."
        print(success_text)

    print_lines_slowly("\n".join(textwrap.wrap(task_desc)))

    # The returned lesson_id stays constant if we are supposed
    # to stay on this task because of a failed input.
    next_lesson_id = lesson_id
    while next_lesson_id == lesson_id:
        input_result = handle_input(json.loads(input_desc))

        # Now, check just before submission if the lessons changed
        # from underneath us. We're a blocking application, so this
        # is the best place to put this check. If the lessons did
        # change, get out and notify the user instead of crashing.
        if monitor.reloaded:
            print("Service indicated that lessons were reloaded, "
                  "bailing out now.")
            return None

        (responses,
         next_lesson_id) = service.call_attempt_lesson_remote_sync(task_name,
                                                                   lesson_id,
                                                                   input_result)

        for response in json.loads(responses):
            show_response(response)

    return next_lesson_id


def practice_task(service, monitor, task, _, entry):
    """Practice the task named :task:"""
    lesson_id = entry

    # practice_lesson_in_task will return None when there are no more
    # tasks to complete in this lesson, for whatever reason.
    while lesson_id is not "":
        lesson_id = practice_lesson_in_task(service,
                                            monitor,
                                            task,
                                            lesson_id)


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
        unlocked_tasks = [("showmehow", "Show me how to do things...", "showmehow")]
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

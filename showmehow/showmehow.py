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
import select
import sys
import textwrap
import threading
import time

try:
    from Queue import Queue
except ImportError:
    from queue import Queue

from collections import defaultdict, OrderedDict

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


def handle_input_choice(choices, _):
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


def handle_input_external_events(settings, monitor):
    """Handle external events input."""
    event_status = {
        "occurred": False
    }

    def signal_handler(monitor, name):
        """Return a function to handle a signal."""
        print("Signal " + name + " happened in main thread")
        event_status["occurred"] = True

    monitor.monitor_signal("lesson-events-satisfied", signal_handler)

    # Continually pump events until the flag is set
    while not event_status["occurred"]:
        monitor.dispatch_queued_signal_events()

    return ""

_INPUT_ACTIONS = {
    "choice": handle_input_choice,
    "text": handle_input_text(""),
    "console": handle_input_text("$ "),
    "external_events": handle_input_external_events
}

def display_input_choice(choices):
    """Display available choices."""
    choices = OrderedDict(choices)
    for index, desc in enumerate(choices.values()):
        print("({}) {}".format(index + 1, desc["text"]))

    sys.stdout.write("Choice: ")
    sys.stdout.flush()


def display_input_prompt(prompt):
    """Display prompt."""
    def _internal(*args):
        """Internal func."""
        del args

        if prompt:
            sys.stdout.write(prompt + " ")
            sys.stdout.flush()

    return _internal


_DISPLAY_INPUT_ACTIONS = {
    "choice": display_input_choice,
    "text": display_input_prompt(">"),
    "console": display_input_prompt("$")
}

def display_input(input_desc):
    """Display a prompt to the user depending on the input type."""
    try:
        handler = _DISPLAY_INPUT_ACTIONS[input_desc["type"]]
    except KeyError:
        return

    return handler(input_desc["settings"])


def handle_user_input_choice(text, choices):
    """Handle a choice by the user.

    If the user makes a wrong choice, show input_desc again.
    """
    choices = OrderedDict(choices)

    try:
        selected_index = int(text) - 1
    except ValueError:
        selected_index = len(choices)

    if selected_index < len(choices):
        return list(choices.keys())[selected_index]
    else:
        return None


def handle_user_input_text(text, *args):
    """Handle some raw textual input by the user."""
    del args

    converted = text.strip().lstrip()
    if len(converted):
        return converted
    else:
        return None


def handle_user_input_external_events(*args):
    """Handle user input when an external event happens."""
    del args
    return ""


_USER_INPUT_ACTIONS = {
    "choice": handle_user_input_choice,
    "text": handle_user_input_text,
    "console": handle_user_input_text,
    "external_events": handle_user_input_external_events
}


def handle_input(text, input_desc):
    """Given some input type, handle the input."""
    try:
        return _INPUT_ACTIONS[input_desc["type"]](text,
                                                  input_desc["settings"])
    except KeyError:
        raise RuntimeError("Don't know how to handle input type " +
                           input_desc["type"])


_INPUT_STATE_TRANSITIONS = defaultdict(lambda: "waiting",
                                       external_events="waiting_lesson_events")


class PracticeTaskStateMachine(object):
    """A state machine representing a currently-practiced task.

    This state machine has the following states:
      (F) -> Fetching task description
      (W) -> Waiting on input
      (S) -> Submit input
      (E) -> Exiting

    The transitions are defined as follows:
      F -> W
      W -> S, W
      S -> F, E
    ."""

    def __init__(self, service, lesson, task):
        """Initialise this state machine with the service.

        Connect to the relevant signals to handle state transitions.
        """
        super(PracticeTaskStateMachine, self).__init__()

        self._service = service
        self._lesson = lesson
        self._task = task
        self._current_input_desc = None
        self._loop = GLib.MainLoop()
        self._state = "fetching"

        self._service.connect("lessons-changed", self.handle_lessons_changed)
        self._service.connect("lesson-events-satisfied", self.lesson_events_satisfied)
        GLib.io_add_watch(sys.stdin.fileno(),
                          GLib.PRIORITY_DEFAULT,
                          GLib.IO_IN,
                          self.handle_user_input)

        # Display content for the entry point
        self._service.call_get_task_description(self._lesson,
                                                self._task,
                                                None,
                                                self.handle_task_description_fetched)

    def start(self):
        """Start the state machine and the underlying main loop."""
        return self._loop.run()

    def handle_lessons_changed(self, *args):
        """Handle lessons changing underneath us."""
        del args

        print("Lessons changed - aborting")
        self._loop.quit()

    def lesson_events_satisfied(self, _, lesson, task):
        """Respond to events happening on lesson."""
        if (self._state == "waiting_lesson_events" and
            self._lesson == lesson and self._task == task):
            self._state = "submit"
            self._service.call_attempt_lesson_remote(self._lesson,
                                                     self._task,
                                                     "",
                                                     None,
                                                     self.handle_attempt_lesson_remote)

    def handle_task_description_fetched(self, source, result):
        """Finish getting the task description and move to W."""
        assert self._state == "fetching"

        try:
            task_desc, input_desc = self._service.call_get_task_description_finish(result)
        except Exception as error:
            print("Getting task description for {} failed: {}".format(self._task,
                                                                      error))

        print_lines_slowly("\n".join(textwrap.wrap(task_desc)))
        self._current_input_desc = json.loads(input_desc)
        self._state = _INPUT_STATE_TRANSITIONS[self._current_input_desc["type"]]
        display_input(self._current_input_desc)

    def handle_attempt_lesson_remote(self, source, result):
        """Finish handling the lesson and move to F or E."""
        assert self._state == "submit"

        try:
            responses, next_task_id = self._service.call_attempt_lesson_remote_finish(result)
        except Exception as error:
            print("Internal error in attempting {}, {}".format(self._task,
                                                               error))

        for response in json.loads(responses):
            show_response(response)

        if next_task_id == self._task:
            display_input(self._current_input_desc)
            self._state = _INPUT_STATE_TRANSITIONS[self._current_input_desc["type"]]
        elif next_task_id == "":
            self._loop.quit()
        else:
            self._state = "fetching"
            self._current_input_desc = None
            self._task = next_task_id
            self._service.call_get_task_description(self._lesson,
                                                    self._task,
                                                    None,
                                                    self.handle_task_description_fetched)

    def handle_user_input(self, stdin_fd, events):
        """Handle user input from stdin.

        Input could happen at any time, so if it does and we're not ready
        just return and wait for it to happen again.
        """
        if not (events & GLib.IO_IN):
            return True

        if self._state == "waiting":
            # Just get one line from the standard in
            user_input = sys.stdin.readline()

            try:
                input_handler = _USER_INPUT_ACTIONS[self._current_input_desc["type"]]
            except KeyError:
                raise RuntimeError("Don't know how to handle input type " +
                                   self._current_input_desc["type"])

            converted_input = input_handler(user_input,
                                            self._current_input_desc["settings"])

            # Two possible state transitions, W -> W
            # or W -> S. W -> W happens if input_handler
            # returns None, otherwise we return the result to
            # the service and switch to S.
            if not converted_input:
                display_input_prompt(self._current_input_desc)
            else:
                # Submit this to the service and wait for the result
                self._state = "submit"
                self._service.call_attempt_lesson_remote(self._lesson,
                                                         self._task,
                                                         converted_input,
                                                         None,
                                                         self.handle_attempt_lesson_remote)
        return True


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
        task, desc, entry = [
            t for t in unlocked_tasks if t[0] == arguments.task
        ][0]
    except IndexError:
        if arguments.task:
            print_lines_slowly("I don't know how to do task {}".format(arguments.task))
        else:
            print_lines_slowly("Hey, how are you? I can tell you about the following tasks:")
        return show_tasks(unlocked_tasks)

    return PracticeTaskStateMachine(service, task, entry).start()

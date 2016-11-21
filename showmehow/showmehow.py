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

try:
    import readline
except ImportError:
    pass

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


def display_input_prompt(prompt):
    """Display prompt."""
    def _internal(*args):
        """Internal func."""
        del args

        if prompt:
            sys.stdout.write(prompt + " ")
            sys.stdout.flush()

    return _internal


def display_input():
    """Display a prompt to the user depending on the input type."""
    try:
        handler = display_input_prompt("$")
    except KeyError:
        return

    return handler()


def handle_user_input_text(text, *args):
    """Handle some raw textual input by the user."""
    del args

    converted = text.strip()
    if len(converted):
        return converted
    else:
        return None


_INPUT_STATE_TRANSITIONS = defaultdict(lambda: "waiting",
                                       external_events="waiting_lesson_events")


def find_task_json(json_file, lesson, task):
    """Find a descriptor associated with a given lesson and task."""
    return [l for l in json_file if l["name"] == lesson][0]["practice"][task]


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

    def __init__(self, service, lessons, lesson, task):
        """Initialise this state machine with the service.

        Connect to the relevant signals to handle state transitions.
        """
        super(PracticeTaskStateMachine, self).__init__()

        self._service = service
        self._lesson = lesson
        self._lessons = lessons
        self._task = task
        self._current_input_desc = None
        self._loop = GLib.MainLoop()
        self._state = "fetching"
        self._session = 0

        self._service.connect("lessons-changed", self.handle_lessons_changed)
        GLib.io_add_watch(sys.stdin.fileno(),
                          GLib.PRIORITY_DEFAULT,
                          GLib.IO_IN,
                          self.handle_user_input)

        # Display content for the entry point
        self.handle_task_description_fetched(find_task_json(self._lessons, self._lesson, self._task))

    def __enter__(self):
        """Enter the context of this PracticeTaskStateMachine.

        This might involve opening a session with the service if
        the underlying lesson requires it.
        """
        requires_session = next((l.get("requires_session") for l in self._lessons
                                if l["name"] == self._lesson), False)

        if requires_session:
            self._session = self._service.call_open_session_sync(None)

        return self

    def __exit__(self, exc_type, value, traceback):
        """Exit the context of this PracticeTaskStateMachine.

        If we have a session open, close it.
        """
        del exc_type
        del value
        del traceback

        if self._session:
            self._session = self._service.call_close_session_sync(self._session, None)

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
            self._service.call_attempt_lesson_remote(self._session,
                                                     self._lesson,
                                                     self._task,
                                                     "",
                                                     None,
                                                     self.handle_attempt_lesson_remote)

    def handle_task_description_fetched(self, task_desc):
        """Finish getting the task description and move to W."""
        assert self._state == "fetching"

        show_response_scrolled(task_desc["task"])
        self._state = "waiting"
        display_input()

    def handle_attempt_lesson_remote(self, source, result):
        """Finish handling the lesson and move to F or E."""
        assert self._state == "submit"

        try:
            attempt_result_json = self._service.call_attempt_lesson_remote_finish(result)
        except Exception as error:
            raise SystemExit("Internal error in attempting {}, {}\n".format(self._task,
                                                                            error))

        # Look up the response in the lessons descriptor and see if there is a next task
        attempt_result = json.loads(attempt_result_json)
        result = attempt_result["result"]
        responses = attempt_result["responses"]
        result_desc = find_task_json(self._lessons, self._lesson, self._task)["effects"][result]
        next_task_id = result_desc.get("move_to", self._task)
        completes_lesson = result_desc.get("completes_lesson", False)

        # Print any relevant responses, wrapped
        for response in responses:
            show_response(response)

        # Print the reply
        show_response_scrolled(result_desc["reply"])

        if completes_lesson:
            self._loop.quit()
        elif next_task_id == self._task:
            display_input()
            self._state = "waiting"
        else:
            self._state = "fetching"
            self._task = next_task_id
            self.handle_task_description_fetched(find_task_json(self._lessons, self._lesson, self._task))

    def handle_user_input(self, stdin_fd, events):
        """Handle user input from stdin.

        Input could happen at any time, so if it does and we're not ready
        just return and wait for it to happen again.
        """
        if not (events & GLib.IO_IN):
            return True

        if self._state == "waiting":
            # Just get one line from the standard in without the line break
            user_input = sys.stdin.readline().rstrip("\n")

            # Submit this to the service and wait for the result
            self._state = "submit"
            self._service.call_attempt_lesson_remote(self._session,
                                                     self._lesson,
                                                     self._task,
                                                     user_input,
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
        sys.stderr.write("Service warning: {}\n".format(warning[0]))

    return service


def noninteractive_predefined_script(arguments):
    """Script to follow if we are non-interactive.

    This does not create a service instance. Instead, it just gives
    two canned responses depending on the arguments.

    If no arguments are given, show a mock response for what unlocked_tasks
    would be.

    If an argument is given, show a mock response for what "showmehow showmehow"
    would do.

    The reason we have this is that we cannot connect to the service
    within a child process of the service - that just hangs. We only care
    about specific canned responses, so just use those.
    """
    if not arguments.task:
        print("Hey, how are you? I can tell you about the following tasks:\n")
        show_tasks([("showmehow", "Show me how to do things...", "showmehow")])
    else:
        task_desc = "'showmehow' is a command that you can type, just like any other command. Try typing it and see what happens."
        success_text = "That's right! Though now you need to tell showmehow what task you want to try. This is called an 'argument'. Try giving showmehow an argument so that it knows what to do. Want to know what argument to give it? There's only one, and it just told you what it was."
        print(task_desc)
        print(success_text)


def main(argv=None):
    """Entry point. Parse arguments and start the application."""
    parser = argparse.ArgumentParser('showmehow - Show me how to do things')
    parser.add_argument('task',
                        nargs='?',
                        metavar='TASK',
                        help='TASK to perform',
                        type=str)

    arguments = parser.parse_args(argv or sys.argv[1:])

    lessons_path = os.path.join(os.path.dirname(__file__), 'lessons.json')
    with open(lessons_path) as lessons_stream:
        lessons = json.load(lessons_stream)

    if os.environ.get("NONINTERACTIVE"):
        return noninteractive_predefined_script(arguments)
    else:
        service = create_service()
        task_name_desc_pairs = {
            l["name"]: l["desc"]
            for l in lessons
        }
        task_name_entry_pairs = {
            l["name"]: l["entry"]
            for l in lessons
        }

        unlocked_tasks = [
            [t, task_name_desc_pairs[t], task_name_entry_pairs[t]]
            for t in ['showmehow', 'joke', 'readfile', 'breakit', 'changesetting', 'playsong', 'navigation', 'text']
        ]

    try:
        task, desc, entry = [
            t for t in unlocked_tasks if t[0] == arguments.task
        ][0]
    except IndexError:
        if arguments.task:
            show_response_scrolled("I don't know how to do task {}".format(arguments.task))
        else:
            show_response_scrolled("Hey, how are you? I can tell you about the following tasks:")
        return show_tasks(unlocked_tasks)

    with PracticeTaskStateMachine(service, lessons, task, entry) as machine:
        machine.start()

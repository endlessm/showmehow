# /showmehow/showmehow.py
#
# Copyright (c) 2016-2017 Endless Mobile Inc.
#
# showmehow - entrypoint
"""Entry point for showmehow."""

import argparse
import atexit
import errno
import itertools
import json
import os
import re
import readline
import sys
import textwrap
import time

from collections import (defaultdict, namedtuple)

import gi

gi.require_version("CodingGameService", "1.0")
gi.require_version("Showmehow", "1.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gio", "2.0")

from gi.repository import (CodingGameService, GLib, Gio, Showmehow)

# Assign 'input' to raw_input if running on Python 2
try:
    input = raw_input
except NameError:
    pass


readline.parse_and_bind("tab: complete")

_PAUSECHARS = ".?!:"


def in_blue(text):
    """Wrap text using ANSI blue color code."""
    blue = '\033[95m'
    end = '\033[0m'
    return blue + text + end


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
    # Preserve paragraphs in original text
    paragraphs = value.split("\n\n")
    for paragraph in paragraphs:
        lines = textwrap.wrap(paragraph, width=68)
        for line in lines:
            print(line)


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
        print_message_slowly_and_wait(in_blue(text), self._wait_time)


def show_response_scrolled(value):
    """Print scrolled text.

    Lines are split into separate paragraphs on \n first before wrapping. This
    is to enable newlines to be printed correctly without extraneous whitespace
    on either side.
    """
    print_lines_slowly(in_blue("\n".join(itertools.chain.from_iterable([
        textwrap.wrap(v)
        for v in value.splitlines()
    ]))))

def show_raw_response(value):
    """Print text as-is."""
    print(value)


_RESPONSE_ACTIONS = {
    "raw": show_raw_response,
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
    """Display a prompt to the user depending on the input type.

    Because this function calls raw_input, it will block the event loop,
    which in the current design is fine because we don't need to respond
    to external events.
    """
    return input("$ ")


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


def _run_event_side_effect(effect, coding_game_service):
    """Dispatch an external event for coding-game-service."""
    try:
        coding_game_service.call_external_event_sync(effect["value"], None)
    except GLib.Error as error:
        # This is an error stating that the service was not interested
        # in this event right now. In that case, we don't care. Continue.
        if error.matches("coding-game-service", 2):
            pass


_SIDE_EFFECT_DISPATCH = {
    "event": _run_event_side_effect
}


def dispatch_side_effect(effect, coding_game_service):
    """Cause :effect: to be dispatched with :service:."""
    return _SIDE_EFFECT_DISPATCH[effect["type"]](effect, coding_game_service)


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

    def __init__(self, service, coding_game_service, lessons, lesson, task):
        """Initialise this state machine with the service.

        Connect to the relevant signals to handle state transitions.
        """
        super(PracticeTaskStateMachine, self).__init__()

        self._service = service
        self._coding_game_service = coding_game_service
        self._service.connect("lessons-changed", self.handle_lessons_changed)
        self._loop = GLib.MainLoop()
        self._lessons = lessons
        self._session = -1
        self._initialize(lesson, task)

    def __enter__(self):
        """Enter the context of this PracticeTaskStateMachine.

        This might involve opening a session with the service if
        the underlying lesson requires it.
        """
        self._session = self._service.call_open_session_sync(self._lesson, None)

        return self

    def __exit__(self, exc_type, value, traceback):
        """Exit the context of this PracticeTaskStateMachine.

        If we have a session open, close it.
        """
        del exc_type
        del value
        del traceback

        if self._session != -1:
            self._session = self._service.call_close_session_sync(self._session, None)

    def _initialize(self, lesson, task):
        """Initialise the lesson state of showmehow and go to the first task."""
        last_session = self._session

        self._session = -1
        self._lesson = lesson
        self._task = task
        self._state = "fetching"

        # If we had a session open, close it and reopen one for this task
        if last_session != -1:
            self._service.call_close_session_sync(last_session, None)
            self._session = self._service.call_open_session_sync(self._lesson, None)

            # Display content for the entry point
            self._show_next_task()

    def _show_next_task(self):
        """Start the very first part of the state machine."""
        self.handle_task_description_fetched(find_task_json(self._lessons,
                                                            self._lesson,
                                                            self._task))

    def start(self):
        """Start the state machine and the underlying main loop."""
        try:
            GLib.idle_add(self._show_next_task)
            return self._loop.run()
        except KeyboardInterrupt:
            self.quit()

    def quit(self):
        """Quit the main loop and print message."""
        print('See you later!')
        sys.exit(0)

    def handle_lessons_changed(self, *args):
        """Handle lessons changing underneath us."""
        del args

        print("Lessons changed - aborting")
        self.quit()

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
        self.handle_user_input(display_input())

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

        # Do any side effects now if they are present
        self._state = "running_side_effects"
        side_effects = result_desc.get("side_effects", list())
        for side_effect in side_effects:
            dispatch_side_effect(side_effect, self._coding_game_service)

        if completes_lesson:
            # Regardless of what the lesson is, fire this event so that
            # the game service can know that *a* lesson completed.
            dispatch_side_effect({
                "type": "event",
                "value": "showmehow-lesson-completed"
            }, self._coding_game_service)
            self._loop.quit()
        elif next_task_id == self._task:
            self._state = "waiting"
            self.handle_user_input(display_input())
        else:
            self._state = "fetching"
            self._task = next_task_id
            self._show_next_task()

    def handle_user_input(self, user_input):
        """Handle user input from readline."""

        # If it is 'quit' or 'exit', exit showmehow
        if user_input in ('quit', 'exit'):
            self.quit()
            return

        # If the user types 'showmehow' and the lesson is not 'showmehow'
        # then exit showmehow as well, but also print its usage. This will
        # give the impression that we're going back to the top level
        if user_input.strip() == "showmehow" and self._lesson != "showmehow":
            show_response_scrolled("Having fun? You can do the following tasks:")
            show_tasks(get_unlocked_tasks(self._lessons))
            self.quit()
            return

        # If the user types 'showmehow X' we should go to that task.
        if user_input.startswith("showmehow") and self._lesson != "showmehow":
            _, requested_lesson = re.split(r"\s+", user_input, maxsplit=1)
            lesson, task = find_task_or_report_error(get_unlocked_tasks(self._lessons),
                                                     requested_lesson)
            self._initialize(lesson, task)
            return

        # Submit this to the service and wait for the result
        self._state = "submit"
        self._service.call_attempt_lesson_remote(self._session,
                                                 self._lesson,
                                                 self._task,
                                                 user_input,
                                                 None,
                                                 self.handle_attempt_lesson_remote)
        return True


def print_name_detail_pair(task):
    """Given a task tuple, print a name and detail pair."""
    print("    [{task[0]}] - {task[1]}".format(task=task))

def show_tasks(tasks):
    """Show tasks that can be done in the terminal."""
    print_lines_slowly(in_blue("For beginners:"))
    for task in tasks:
        if task[3] == "beginner":
            print_name_detail_pair(task)

    print_lines_slowly(in_blue("If you're a little more confident:"))
    for task in tasks:
        if task[3] == "intermediate":
            print_name_detail_pair(task)

    print_lines_slowly(in_blue("If you're ready for a challenge:"))
    for task in tasks:
        if task[3] == "advanced":
            print_name_detail_pair(task)

    print_lines_slowly(in_blue("To run any of these lessons, simply enter the command’s name. For example, you could type ‘showmehow breakit’ (without the quotation marks) and then hit enter."))

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


def create_coding_game_service():
    """Create a CodingGameService Proxy instance."""
    return CodingGameService.CodingGameServiceProxy.new_for_bus_sync(Gio.BusType.SESSION,
                                                                     0,
                                                                     "com.endlessm.CodingGameService.Service",
                                                                     "/com/endlessm/CodingGameService/Service")

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
        show_tasks([
            ("showmehow", "Show me how to do things...", "showmehow", "beginner")
        ])
    else:
        task_desc = "'showmehow' is a command that you can type, just like any other command. Try typing it and see what happens."
        success_text = "That's right! Though now you need to tell showmehow what task you want to try. This is called an 'argument'. Try giving showmehow an argument so that it knows what to do. Want to know what argument to give it? There's only one, and it just told you what it was."
        print(task_desc)
        print(success_text)


def print_banner():
    """Print a small banner informing the user how to continue.

    However, we don't want to print this banner if we have already run.
    """
    first_run_file = os.path.join(GLib.get_user_config_dir(), 'com.endlessm.Showmehow', '.first-run')
    if os.path.exists(first_run_file):
        return

    # Write the file - it is okay if the containing directory exists
    try:
        os.makedirs(os.path.dirname(first_run_file))
    except OSError as error:
        if error.errno != errno.EEXIST:
            raise error

    with open(first_run_file, 'w') as fileobj:
        fileobj.write('')

    print("""[STATUS] Loading""")
    time.sleep(0.2)
    print("""[STATUS] Fetching content""")
    time.sleep(0.4)
    print("""[STATUS] Transforming system""")
    time.sleep(0.1)
    print("""[DONE]\n\n"""
          """Welcome to 'showmehow'!\n"""
          """\n""",
          """We'll show you what's behind the curtains on your system.\n"""
          """\n"""
          """To exit at any time, type 'exit' or 'quit' and press 'enter'.\n"""
          """Have a lot of fun!\n"""
          """\n\n""")


def load_lessons():
    """Load lessons from the specified JSON file."""
    lessons_path = os.path.join(os.path.dirname(__file__), 'lessons.json')
    with open(lessons_path) as lessons_stream:
        return json.load(lessons_stream)


UnlockedTaskDetail = namedtuple("UnlockedTaskDetail", "desc entry level")

def get_unlocked_tasks(lessons):
    """Get available tasks."""
    task_name_detail_pairs = {
        l["name"]: UnlockedTaskDetail(l["desc"], l["entry"], l["level"])
        for l in lessons
    }

    settings = Gio.Settings.new('com.endlessm.showmehow')
    return [
        [
            t,
            task_name_detail_pairs[t].desc,
            task_name_detail_pairs[t].entry,
            task_name_detail_pairs[t].level
        ]
        for t in settings.get_value('unlocked-lessons')
        if t in task_name_desc_pairs and t in task_name_entry_pairs
    ]


def find_task_or_report_error(unlocked_tasks, requested_task):
    """Attempt to find requested_task in unlocked_tasks or report an error."""
    try:
        task, desc, entry, level = [
            t for t in unlocked_tasks if t[0] == requested_task
        ][0]
        return (task, entry)
    except IndexError:
        if requested_task:
            show_response_scrolled("I don't know how to do task {}".format(requested_task))
        elif len(unlocked_tasks) == 0:
            show_response_scrolled("I can't show you anything right now, sorry.")
        else:
            show_response_scrolled("Hey, how are you? I can tell you about the following tasks:")
        show_tasks(unlocked_tasks)
        return (None, None)


def main(argv=None):
    """Entry point. Parse arguments and start the application."""
    parser = argparse.ArgumentParser('showmehow - Show me how to do things')
    parser.add_argument('task',
                        nargs='?',
                        metavar='TASK',
                        help='TASK to perform',
                        type=str)
    parser.add_argument('--list',
                        help='Display list of known commands',
                        action='store_true')
    arguments = parser.parse_args(argv or sys.argv[1:])
    lessons = load_lessons()

    if os.environ.get("NONINTERACTIVE"):
        return noninteractive_predefined_script(arguments)
    else:
        service = create_service()
        coding_game_service = create_coding_game_service()
        unlocked_tasks = get_unlocked_tasks(lessons)

    if arguments.list:
        for t in unlocked_tasks:
            print(t[0])
        sys.exit(0)

    # Only print the banner when showmehow is actually useful
    if len(unlocked_tasks) != 0:
        print_banner()

    task, entry = find_task_or_report_error(unlocked_tasks, arguments.task)
    if not task or not entry:
        return

    with PracticeTaskStateMachine(service, coding_game_service, lessons, task, entry) as machine:
        machine.start()

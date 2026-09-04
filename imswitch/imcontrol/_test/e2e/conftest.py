"""Shared pytest behaviour for the e2e suite."""


# Colour the progress percentage yellow once something has been skipped, so a
# skipped test no longer shows a green [ 66%] next to a yellow SKIPPED.
#
# pytest does not do this on its own. TerminalReporter._determine_main_color
# turns the percentage yellow for "warnings", "xpassed" and unknown outcomes,
# and red for "failed" and "error" - but "skipped" is not in that list, so a
# run whose tests otherwise pass stays green throughout.
#
# That matters more here than in a normal suite: these tests skip whenever the
# hardware they need is absent, so a skip is the difference between "the rig is
# fine" and "the rig was never asked". It should be visible at a glance.
#
# The colour is cumulative, matching how pytest already treats red: from the
# first skipped test onwards the percentage stays yellow. A later failure still
# wins, because only green is upgraded.
#
# Hooked at session start, not at configure: the terminal reporter registers
# itself during configure, and a conftest's pytest_configure runs before that,
# where get_plugin("terminalreporter") still answers None.
#
# This wraps a private method, so it degrades to doing nothing rather than
# breaking the run if a future pytest renames it.
def pytest_sessionstart(session):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")

    if reporter is None or not hasattr(reporter, "_determine_main_color"):
        return

    original = reporter._determine_main_color

    def determine_main_color(unknown_type_seen):
        color = original(unknown_type_seen)

        if color == "green" and "skipped" in reporter.stats:
            return "yellow"

        return color

    reporter._determine_main_color = determine_main_color

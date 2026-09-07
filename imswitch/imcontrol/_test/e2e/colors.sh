# Shared colour setup for the e2e runner scripts. Sourced, never executed.
#
# pytest turns colour off when stdout is not a tty, and it never is in these
# runners: both ssh and docker exec are run without one. Force it back on, but
# only while we are actually on a terminal, so redirecting a run to a file
# stays clean text.
#
# Defines COLOR: the pytest flag on a terminal, empty otherwise. The scripts
# pass it unquoted, so the empty case expands to no argument at all rather than
# to an empty one pytest would reject.

COLOR=""
if [ -t 1 ]; then COLOR="--color=yes"; fi
#!/bin/sh
# Runs the command at low CPU priority so that the server's other services keep their share.
set -eu
exec nice -n 10 "$@"

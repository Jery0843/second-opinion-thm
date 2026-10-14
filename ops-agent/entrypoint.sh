#!/bin/sh
# Writes the user flag at container start, from the USER_FLAG env var
# (generated fresh per deployment by setup.sh) -- never baked into the
# image, so the image itself carries no secret.
set -e

: "${USER_FLAG:?USER_FLAG must be set}"
echo "${USER_FLAG}" > /home/user.txt
chmod 644 /home/user.txt

exec python app.py

# Notes for Claude

## The page

Keep explanatory text out of the UI. A label and its control should be enough: do not add hint lines, captions,
intro paragraphs or placeholder asides that describe what a control does or how the app works. Text earns its
place only when it tells the user something they cannot see and need in order to act (an error, something not
installed and the command that installs it). Explanations belong in the README.

## After a change

Restart the web server yourself after changing this project, without being asked, so that the running app is the
code in the checkout (the page's files are read from disk on each request; the Python is not):

- `systemctl --user restart callie` when the systemd unit is installed (`./callie status` says so);
- `./callie start` otherwise (it stops a running server first).

Do not run `./callie start` while the unit is installed: the server it starts holds the port, and the unit
fails and restarts in a loop behind it. `./callie stop` ends such a server and the unit takes the port.

Then see that it answers: `./callie status`.

## Tests

`python3 -m unittest discover -s tests -t .` (plain functions with asserts, nothing to install).

## When a change is done

Commit it and push it, every time, without being asked: once the tests pass and the restarted server answers.
One commit for the change, with only that change's files in it. If the checkout holds other work that is not
committed, say so and ask before that goes out too (the repository on GitHub is public).

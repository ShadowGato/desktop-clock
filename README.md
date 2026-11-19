# Desktop Clock

A borderless clock for Windows. It has no title bar. Drag the digits or the glass to move it. Drag a corner to resize it. The time and the date scale with the window.

The gear turns always-on-top on or off, and shows or hides each slider. Glass runs from a solid plate at 0 to clear at 100. Font brightness runs from invisible at 0 to the full digit colors at 100. The two sliders do not move together.

Close the window with the × or with Escape.

## Run

Requires Python 3 with tkinter, which is included in the standard Windows installer. Segoe UI is the face used for the time and the date.

```
pythonw desktop_clock.py
```

`python desktop_clock.py` also works. `pythonw` keeps the console window from opening.

## Smallest size

The window will not shrink past the full time and the full date. With both sliders showing, that floor is about 110 by 76 pixels. Hiding the sliders lets it get shorter. The type does not go below 6pt, which is the smallest Segoe UI size that still draws every digit.

## How the glass works

This is Windows-only. The plate is a second window placed behind the clock. Pixels painted `#1a1d21` are a color key, so those parts of the clock are not drawn and clicks on them pass through to the plate. The digits, the gear, the close mark, and the slider troughs stay on the front window.

# Warframe-EE.log-reader
Program to parse EE.log reader for specific messages

## How it works

This program parses the EE.log file in warframe for specific messages via regex and displays them via tkinter (may be changed in the future as this code is still a WIP)
This program automatically grabs the log file from it's default location and also accepts uploads of other EE.log files in case the user saved them, there is an option to autorefresh so periodically the program checks for updates in the file.

With the right mouse button an additional context menu can be accessed, this enables to export to csv, copy row and more.

## How to install

Since tkinterdnd should be the only package outside of the standard distribution to install it you will need to run
```bash
pip install tkinterdnd2
```
For linux users sometimes tkinter itself might not in the standard distribution so you might need to use thjese commands
For debian based distros such as ubuntu
```bash
sudo apt-get install python3-tk
```
For red hat based distros such as fedora
```bash
sudo dnf install python3-tkinter
```
For arch based distros
```bash
sudo pacman -S tk
```
For openSUSE
```bash
sudo zypper install python3-tk
```


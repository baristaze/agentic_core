# The demo, recorded for the README. From the repository's root:
#
#   vhs demo/dreamcatcher.tape
#
# It runs `make demo` and writes demo/dreamcatcher.gif, from the first
# act's title to the closing ledger.
Output demo/dreamcatcher.gif

Set Shell "bash"
Set FontSize 14
Set LineHeight 1.15
Set Width 1136
Set Height 712
Set Padding 12
Set Framerate 15
Set WaitTimeout 120s

Hide
Type "make demo"
Enter
Wait+Screen /Act 1/
Show
Wait+Screen /The ledger closes/
Sleep 5s

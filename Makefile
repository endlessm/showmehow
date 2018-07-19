PREFIX?=/usr

all:
	true

install:
	install -D completion/showmehow $(PREFIX)/share/bash-completion/completions
	install bin/showmehow $(PREFIX)/bin
	install bin/remindmehow $(PREFIX)/bin

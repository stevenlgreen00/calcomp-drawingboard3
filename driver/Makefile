# Out-of-tree build for the CalComp DrawingBoard III serio driver.
#
#   make              build calcomp_drv.ko against the running kernel
#   make KDIR=...     build against another kernel tree
#   make test         run the userspace protocol test against captured packets
#   sudo make install install into /lib/modules/$(uname -r)/updates and run depmod
#   make clean

KDIR ?= /lib/modules/$(shell uname -r)/build
PWD  := $(CURDIR)

all: modules

modules:
	@test -d "$(KDIR)" || { echo "No kernel build tree at $(KDIR)."; \
		echo "Install it with: sudo apt install linux-headers-$$(uname -r)"; exit 1; }
	$(MAKE) -C $(KDIR) M=$(PWD) modules

install: modules
	$(MAKE) -C $(KDIR) M=$(PWD) INSTALL_MOD_DIR=updates modules_install
	depmod -a

clean:
	@if [ -d "$(KDIR)" ]; then $(MAKE) -C $(KDIR) M=$(PWD) clean; fi
	rm -f test/test_proto test/vectors.txt

test: test/test_proto test/vectors.txt
	./test/test_proto test/vectors.txt

test/test_proto: test/test_proto.c calcomp_proto.h
	$(CC) -Wall -Wextra -Werror -O2 -I. -o $@ test/test_proto.c

test/vectors.txt: test/make_vectors.py
	python3 -I test/make_vectors.py ../captures > $@

.PHONY: all modules install clean test
.DELETE_ON_ERROR:

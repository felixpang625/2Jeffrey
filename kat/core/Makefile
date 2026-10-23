CXX      ?= g++
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra -I.
DEPS      = katamari_core.cpp vendor/meshoptimizer.cpp

katamari: katamari_cli.cpp $(DEPS) katamari_core.h
	$(CXX) $(CXXFLAGS) -o $@ katamari_cli.cpp $(DEPS)

test: katamari
	python3 test_core.py

clean:
	rm -f katamari

.PHONY: test clean

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra
LDFLAGS = -lstdc++fs -l:libwebp.a -lpthread
SOURCES = main.cpp lodepng.cpp
TARGET = mergeFolders
IMAGE = og-tiler

build: CXXFLAGS += -O2
build: $(TARGET)

debug: CXXFLAGS += -g -O0 -DDEBUG
debug: $(TARGET)

$(TARGET): $(SOURCES)
	$(CXX) $(CXXFLAGS) $(SOURCES) $(LDFLAGS) -o $(TARGET)

clean:
	rm -f $(TARGET)

image:
	docker build -t $(IMAGE) .

test:
	docker run --rm -v "$(CURDIR)":/repo -w /repo $(IMAGE) python3 test/cube/test_cube2tiles.py

.PHONY: build debug clean image test

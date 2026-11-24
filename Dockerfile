# Both stages are on the GDAL image: the same Ubuntu and glibc, and nothing is pulled from Docker Hub
ARG BASE_IMAGE=ghcr.io/osgeo/gdal:ubuntu-small-3.6.3

FROM $BASE_IMAGE AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ make libwebp-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY ./Makefile ./main.cpp ./lodepng.cpp ./lodepng.h ./
RUN make build

FROM $BASE_IMAGE
LABEL maintainer="Zemledelec mgevlich@gmail.com"
LABEL description="OpenGlobus tiles of GeoTIFFs: heights and imagery"
WORKDIR /work
# The package lists are taken in the same step as the packages, a cached old list has gone packages
RUN apt-get update && apt-get upgrade -y && apt-get install -y --fix-missing python3-pip wget \
    && rm -rf /var/lib/apt/lists/*
# The GDAL python of the image is built with NumPy 1
RUN pip3 install rasterio rio-rgbify "numpy<2"
RUN wget https://download.osgeo.org/proj/vdatum/egm08_25/egm08_25.gtx
ADD ./rgbifyff.sh .
COPY --from=build /build/mergeTiles .
ADD ./cube2tiles.py .
RUN cp ./egm08_25.gtx /usr/share/gdal

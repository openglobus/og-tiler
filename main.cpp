/**
 * mergeTiles merges new tiles (.png or .webp) into a tile set: the new tiles are moved in, the tiles that are
 * already there are updated where the new ones have data.
 *
 * Build: make build
 *
 * Run:
 * ./mergeTiles <new tiles> <tile set> [minHeight resolution] [--alpha] [--quality 1..100] [--threads N]
 *
 * Heights:   ./mergeTiles ./dem/__temp__ ./dem -10000 0.1
 * Imagery:   ./mergeTiles ./sat/__temp__ ./sat --alpha
 */
#include "lodepng.h"

#include <webp/decode.h>
#include <webp/encode.h>

#include <experimental/filesystem>
#include <iostream>
#include <vector>
#include <string>
#include <fstream>
#include <algorithm>
#include <condition_variable>
#include <chrono>
#include <deque>
#include <mutex>
#include <thread>
#include <utility>
#include <cstdio>
#include <unordered_set>
#include <dirent.h>
#include <sched.h>
#include <sys/stat.h>

#define ZERO 0.0

#define R 0
#define G 1
#define B 2

namespace fs = std::experimental::filesystem;

// The tiles are merged by several threads, their output goes by lines
std::mutex outputMutex;

#define SAY(text)                                                                                                      \
    {                                                                                                                  \
        std::lock_guard<std::mutex> lock(outputMutex);                                                                 \
        std::cout << text;                                                                                             \
    }


struct DemParams
{
    float minHeight;
    float resolution;
    bool alpha;
    // WebP quality 1..100 of the lossy imagery, 0 is lossless
    int quality;
};

// Lossless WebP effort, the same as cube2tiles.py writes: about 2/3 of the png size and faster than png
#define WEBP_LOSSLESS_QUALITY 25
#define WEBP_LOSSLESS_METHOD 1

inline float height(unsigned char *rgb, int index, const DemParams &demParams)
{
    return demParams.minHeight + demParams.resolution * (rgb[index + R] * 256 * 256 + rgb[index + G] * 256 + rgb[index + B]);
}

inline bool isNoData(unsigned char *rgb, int index, const DemParams &demParams)
{
    return height(rgb, index, demParams) == demParams.minHeight; // Looks like minHeight is NODATA
}

inline bool isZero(unsigned char *rgb, int index, const DemParams &demParams)
{
    return height(rgb, index, demParams) == ZERO;
}

inline bool needUpdate(unsigned char *srcRgb, int index, const DemParams &demParams)
{
    if (srcRgb[index + 3] == 0)
    {
        return false;
    }

    if (demParams.alpha)
    {
        return true;
    }

    return !(isNoData(srcRgb, index, demParams) || isZero(srcRgb, index, demParams));
}

inline bool isDirectory(const char *path)
{
    struct stat sb;
    return stat(path, &sb) != 0 ? false : S_ISDIR(sb.st_mode);
}

void moveFile(const char *src, const char *dst)
{
    std::cout << '.';
    // std::cout << "...move file: " << src << " to " << dst << '\n';
    fs::rename(src, dst);
}

void moveFolder(const char *src, const char *dst)
{
    std::cout << '=';
    // std::cout << "...move folder: " << src << " to " << dst << '\n';
    fs::rename(src, dst);
}

inline bool isWebp(const std::string &path)
{
    return fs::path(path).extension() == ".webp";
}

// RGBA pixels of a .png or .webp file, an error text when it fails
std::string readImage(const std::string &path, std::vector<unsigned char> &rgba, unsigned &width, unsigned &height)
{
    if (!isWebp(path))
    {
        unsigned error = lodepng::decode(rgba, width, height, path);
        return error ? lodepng_error_text(error) : "";
    }

    std::ifstream file(path, std::ios::binary | std::ios::ate);
    std::vector<unsigned char> data(file ? (size_t)file.tellg() : 0);
    file.seekg(0);
    file.read((char *)data.data(), data.size());

    int w, h;
    uint8_t *pixels = data.empty() ? nullptr : WebPDecodeRGBA(data.data(), data.size(), &w, &h);

    if (!pixels)
    {
        return "can not decode webp";
    }

    width = w;
    height = h;

    rgba.assign(pixels, pixels + (size_t)w * h * 4);

    WebPFree(pixels);

    return "";
}

// Writes a temporary file and renames it to the tile, so a stopped merge leaves no cut tile
std::string writeFile(const std::string &path, const unsigned char *data, size_t size)
{
    std::string temp = path + ".tmp";
    {
        std::ofstream file(temp, std::ios::binary | std::ios::trunc);
        file.write((const char *)data, size);

        if (!file)
        {
            return "can not write the file";
        }
    }

    if (std::rename(temp.c_str(), path.c_str()) != 0)
    {
        return "can not rename the file";
    }

    return "";
}

std::string writeImage(const std::string &path, std::vector<unsigned char> &rgba, unsigned width, unsigned height,
                       const DemParams &demParams)
{
    if (!isWebp(path))
    {
        std::vector<unsigned char> png;
        unsigned error = lodepng::encode(png, rgba, width, height);
        return error ? lodepng_error_text(error) : writeFile(path, png.data(), png.size());
    }

    WebPConfig config;
    WebPPicture picture;

    if (!WebPConfigInit(&config) || !WebPPictureInit(&picture))
    {
        return "webp version mismatch";
    }

    // The heights are never lossy
    if (demParams.alpha && demParams.quality > 0)
    {
        config.quality = demParams.quality;
    }
    else
    {
        config.lossless = 1;
        config.exact = 1;
        config.quality = WEBP_LOSSLESS_QUALITY;
        config.method = WEBP_LOSSLESS_METHOD;
    }

    picture.use_argb = 1;
    picture.width = width;
    picture.height = height;

    WebPMemoryWriter writer;
    WebPMemoryWriterInit(&writer);
    picture.writer = WebPMemoryWrite;
    picture.custom_ptr = &writer;

    std::string error;
    if (!WebPPictureImportRGBA(&picture, rgba.data(), width * 4))
    {
        error = "webp import error";
    }
    else if (!WebPEncode(&config, &picture))
    {
        error = "webp encoder error " + std::to_string(picture.error_code);
    }
    else
    {
        error = writeFile(path, writer.mem, writer.size);
    }

    WebPPictureFree(&picture);
    WebPMemoryWriterClear(&writer);

    return error;
}

void mergeImages(const char *src, const char *dst, const DemParams &demParams)
{
    SAY('+');

    std::vector<unsigned char> srcImage;
    std::vector<unsigned char> dstImage;

    unsigned width, height, dstWidth, dstHeight;

    // decode
    std::string srcError = readImage(src, srcImage, width, height);

    if (!srcError.empty())
    {
        SAY("decoder error: " << src << ": " << srcError << std::endl);
        return;
    }

    // The pixels of the new tile that replace the old ones
    size_t pixels = srcImage.size() / 4, updates = 0;
    for (size_t i = 0; i < srcImage.size(); i += 4)
    {
        updates += needUpdate(srcImage.data(), i, demParams);
    }

    // No data: the old tile stays as it is
    if (updates == 0)
    {
        std::remove(src);
        return;
    }

    // Every pixel is replaced: the new tile is the merged one, it is moved over the old one
    if (updates == pixels)
    {
        if (std::rename(src, dst) != 0)
        {
            SAY("can not move: " << src << " to " << dst << std::endl);
        }
        return;
    }

    std::string dstError = readImage(dst, dstImage, dstWidth, dstHeight);

    if (!dstError.empty())
    {
        SAY("decoder error: " << dst << ": " << dstError << std::endl);
        return;
    }

    if (width != dstWidth || height != dstHeight)
    {
        SAY("size mismatch: " << src << " is " << width << "x" << height << ", " << dst << " is " << dstWidth << "x"
                              << dstHeight << std::endl);
        return;
    }

    unsigned char *srcImageArr = srcImage.data();
    unsigned char *dstImageArr = dstImage.data();

    bool changed = false;
    for (size_t i = 0; i < srcImage.size(); i += 4)
    {
        if (needUpdate(srcImageArr, i, demParams) && !std::equal(srcImageArr + i, srcImageArr + i + 4, dstImageArr + i))
        {
            std::copy_n(srcImageArr + i, 4, dstImageArr + i);
            changed = true;
        }
    }

    // The new pixels are the same as the old ones (the same source again): the old tile stays as it is
    if (!changed)
    {
        std::remove(src);
        return;
    }

    // Encode the image
    std::string encodeError = writeImage(dst, dstImage, width, height, demParams);

    if (!encodeError.empty())
    {
        SAY("encoder error: " << dst << ": " << encodeError << std::endl);
        return;
    }

    // The new tile is merged, it is not needed anymore
    std::remove(src);
}

// The extension of the first tile in a folder, empty when there is none. The scripts make the new tiles in a folder
// of the destination (__temp__), it is skipped.
std::string tileFormat(const std::string &path, const std::string &skip = "")
{
    if (!isDirectory(path.c_str()))
    {
        return "";
    }

    bool hasSkip = isDirectory(skip.c_str());

    for (auto it = fs::recursive_directory_iterator(path); it != fs::recursive_directory_iterator(); ++it)
    {
        if (hasSkip && it->status().type() == fs::file_type::directory && fs::equivalent(it->path(), skip))
        {
            it.disable_recursion_pending();
            continue;
        }

        std::string ext = it->path().extension().string();

        if (ext == ".png" || ext == ".webp")
        {
            return ext;
        }
    }

    return "";
}

// The tiles to merge: the folders walk adds them, the threads merge them at the same time
class MergeQueue
{
    std::mutex mutex;
    std::condition_variable ready;
    std::deque<std::pair<std::string, std::string>> jobs;
    bool closed = false;

  public:
    void push(const std::string &src, const std::string &dst)
    {
        {
            std::lock_guard<std::mutex> lock(mutex);
            jobs.emplace_back(src, dst);
        }
        ready.notify_one();
    }

    // No more tiles, the threads finish the rest
    void close()
    {
        {
            std::lock_guard<std::mutex> lock(mutex);
            closed = true;
        }
        ready.notify_all();
    }

    // The next tile, false when there is no more
    bool pop(std::pair<std::string, std::string> &job)
    {
        std::unique_lock<std::mutex> lock(mutex);
        ready.wait(lock, [&]() { return closed || !jobs.empty(); });

        if (jobs.empty())
        {
            return false;
        }

        job = std::move(jobs.front());
        jobs.pop_front();
        return true;
    }
};

struct FolderEntry
{
    std::string name;
    bool directory;
};

// The entries of a folder and their types by one readdir. A stat of every entry is slow on the windows disks
// (/mnt/c), about 2 ms, and the folders of the tiles have hundreds of them.
std::vector<FolderEntry> listFolder(const std::string &path)
{
    std::vector<FolderEntry> entries;

    DIR *dir = opendir(path.c_str());
    if (!dir)
    {
        return entries;
    }

    while (dirent *entry = readdir(dir))
    {
        std::string name = entry->d_name;
        if (name == "." || name == "..")
        {
            continue;
        }

        bool directory = entry->d_type == DT_UNKNOWN ? isDirectory((path + "/" + name).c_str()) : entry->d_type == DT_DIR;
        entries.push_back({name, directory});
    }

    closedir(dir);
    return entries;
}

// Moves the tiles and the folders the destination has not, gives the tiles to merge to the threads
void mergeFolders(const std::string &srcPath, const std::string &dstPath, MergeQueue &queue)
{
    std::unordered_set<std::string> dstNames;
    for (const auto &entry : listFolder(dstPath))
    {
        dstNames.insert(entry.name);
    }

    for (const auto &entry : listFolder(srcPath))
    {
        std::string src_p_str = srcPath + "/" + entry.name;
        std::string dst_p_str = dstPath + "/" + entry.name;
        std::string ext = fs::path(entry.name).extension().string();

        if (!dstNames.count(entry.name))
        {
            if (entry.directory)
            {
                moveFolder(src_p_str.c_str(), dst_p_str.c_str());
            }
            else
            {
                moveFile(src_p_str.c_str(), dst_p_str.c_str());
            }
        }
        else if (entry.directory)
        {
            mergeFolders(src_p_str, dst_p_str, queue);
        }
        else if (ext == ".png" || ext == ".webp")
        {
            queue.push(src_p_str, dst_p_str);
        }
    }
}

// The cpus of the process, docker --cpuset-cpus limits them
unsigned cpuCount()
{
    cpu_set_t set;
    if (sched_getaffinity(0, sizeof(set), &set) == 0)
    {
        return std::max(1, CPU_COUNT(&set));
    }
    return std::max(1u, std::thread::hardware_concurrency());
}

int main(int argc, char *argv[])
{
    std::vector<const char *> args;
    bool alpha = false;
    int quality = 0;

    // Threads of the merged tiles, the cpus by default
    unsigned threads = 0;

    for (int i = 1; i < argc; i++)
    {
        if (std::string(argv[i]) == "--alpha")
        {
            alpha = true;
        }
        else if (std::string(argv[i]) == "--quality" && i + 1 < argc)
        {
            quality = std::stoi(argv[++i]);
        }
        else if (std::string(argv[i]) == "--threads" && i + 1 < argc)
        {
            threads = std::stoi(argv[++i]);
        }
        else
        {
            args.push_back(argv[i]);
        }
    }

    if (args.size() < 2)
    {
        std::cout << "Usage: ./mergeTiles <new tiles> <tile set> [minHeight resolution] [--alpha] [--quality 1..100] "
                     "[--threads N]"
                  << '\n';
        return 1;
    }

    std::cout << "Merge tiles:..." << '\n';
    std::fflush(stdout);

    const char *srcPath = args[0], *dstPath = args[1];

    float minHeight = args.size() > 2 ? std::stof(args[2]) : -10000.0f;
    float resolution = args.size() > 3 ? std::stof(args[3]) : 0.1f;

    if (quality > 0 && !alpha)
    {
        std::cout << "--quality is for the imagery (--alpha), the heights are written lossless" << '\n';
    }

    DemParams demParms = {minHeight, resolution, alpha, quality};

    std::string srcPath_str = srcPath;
    std::string dstPath_str = dstPath;

    // One tile set has one format, png tiles in a webp set (or back) are not loaded by its url
    std::string srcFormat = tileFormat(srcPath_str), dstFormat = tileFormat(dstPath_str, srcPath_str);

    if (!srcFormat.empty() && !dstFormat.empty() && srcFormat != dstFormat)
    {
        std::cout << "format mismatch: " << srcPath_str << " has " << srcFormat << " tiles, " << dstPath_str << " has "
                  << dstFormat << " ones. Make the tiles with --format " << dstFormat.substr(1) << '\n';
        return 1;
    }

    // The progress (. = +) is shown every 0.2 s
    std::mutex flushMutex;
    std::condition_variable flushWake;
    bool finished = false;

    std::thread flusher([&]() {
        std::unique_lock<std::mutex> lock(flushMutex);
        while (!flushWake.wait_for(lock, std::chrono::milliseconds(200), [&]() { return finished; }))
        {
            std::fflush(stdout);
        }
    });

    MergeQueue queue;
    std::vector<std::thread> pool;

    for (unsigned t = 0, count = threads > 0 ? threads : cpuCount(); t < count; t++)
    {
        pool.emplace_back([&]() {
            std::pair<std::string, std::string> job;
            while (queue.pop(job))
            {
                mergeImages(job.first.c_str(), job.second.c_str(), demParms);
            }
        });
    }

    mergeFolders(srcPath_str, dstPath_str, queue);
    queue.close();

    for (auto &thread : pool)
    {
        thread.join();
    }

    {
        std::lock_guard<std::mutex> lock(flushMutex);
        finished = true;
    }
    flushWake.notify_one();
    flusher.join();

    std::cout << '\n' << "Done." << '\n';
}
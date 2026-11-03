# Installing ORemove

This guide walks you through installing **ORemove - AI Powered Vocal Isolator** on Windows, step by step. It takes about 10 minutes, most of it downloading.

> **ORemove is free.** If anyone charged you for it, you've been scammed: only download it from the [official releases page](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases).

**Contents**

1. [Choose your edition](#1-choose-your-edition)
2. [Install the NVIDIA GPU edition](#2-install-the-nvidia-gpu-edition)
3. [Install the CPU edition](#3-install-the-cpu-edition)
4. [Install on a Mac](#4-install-on-a-mac)
5. [First launch](#5-first-launch)
6. [Updating](#6-updating)
7. [Uninstalling](#7-uninstalling)
8. [Where ORemove keeps its files](#8-where-oremove-keeps-its-files)
9. [Installation problems](#9-installation-problems)

---

## 1. Choose your edition

**On a Mac?** There's one Mac edition, for Apple Silicon (M1 or newer). Skip to [Install on a Mac](#4-install-on-a-mac).

On Windows,

ORemove comes in two editions. Pick the one that matches your graphics card.

| | NVIDIA GPU edition | CPU edition |
|---|---|---|
| **For** | PCs with an **NVIDIA** graphics card (GeForce GTX 900 series or newer) | **Any** Windows PC, including **AMD** and **Intel** graphics |
| **Speed** | Fast: a song takes seconds to about a minute | Slow: a song takes several minutes, up to 30+ with the best models |
| **Download** | About 1.9 GB (a small installer that downloads the rest) | About 470 MB (one zip file) |
| **Installs** | Yes, with Start Menu shortcut and uninstaller | No, runs from a folder |

> [!IMPORTANT]
> **GPU acceleration works with NVIDIA graphics cards only.** AMD and Intel graphics are not supported. Choose the CPU edition for those.

### How to check which graphics card you have

1. Press **Ctrl + Shift + Esc** to open **Task Manager**.
2. Click **Performance** (the chart icon on the left).
3. Look at the **GPU** entries in the list. Each one shows the card's name.

If any GPU entry says **NVIDIA**, use the **NVIDIA GPU edition**. Otherwise, use the **CPU edition**.

### Requirements

- Windows 10 or 11, 64-bit
- 8 GB RAM minimum (16 GB recommended)
- Free disk space: about 6 GB for the NVIDIA GPU edition or 2 GB for the CPU edition, plus up to 3 GB for the downloadable models (610–871 MB each, downloaded the first time you use each one). Recommended free space: 12 GB for the NVIDIA GPU edition, 6 GB for the CPU edition
- NVIDIA GPU edition only: an NVIDIA GeForce GTX 900-series or newer card with a current driver

---

## 2. Install the NVIDIA GPU edition

### Step 1: Download the installer

Download **[ORemove-NVIDIA-GPU-Setup.exe](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest/download/ORemove-NVIDIA-GPU-Setup.exe)** (2.5 MB). That's the only file you need: during setup it downloads the rest of ORemove (about 1.9 GB) and checks it. The link always points to the newest version.

> If your browser says the file "isn't commonly downloaded", open the download menu, choose **Keep**, then **Keep anyway**. This appears because ORemove isn't code-signed.

The release page also lists `.bin` files and a `parts.txt` file: those are what the installer downloads for you, so you can ignore them.

### Step 2: Run the installer

1. Open your **Downloads** folder and double-click **ORemove-NVIDIA-GPU-Setup.exe**.
2. If Windows shows **"Windows protected your PC"**, click **More info**, then **Run anyway**.
3. If the installer says **"No NVIDIA graphics card was found on this PC"**, click **No**. Your PC can't use this edition, so install the [CPU edition](#3-install-the-cpu-edition) instead.

### Step 3: Follow the installer

1. **Welcome:** click **Next**.
2. **License Agreement:** read it, select **I accept the agreement**, and click **Next**.
3. **Destination Location:** keep the default folder and click **Next**. No administrator rights are needed; ORemove installs for your user account only.
4. **Additional Tasks:** tick **Create a desktop shortcut** if you want one, then click **Next**.
5. **Ready to Install:** click **Install**. Setup first downloads the rest of ORemove (about 1.9 GB; a few minutes on a fast connection), then installs it.
6. **Finish:** leave **Launch ORemove** ticked and click **Finish**.

Setup removes the parts it downloaded once it's finished. You can delete `ORemove-NVIDIA-GPU-Setup.exe` too.

ORemove is now in your **Start Menu**. Continue with [First launch](#5-first-launch).

---

## 3. Install the CPU edition

### Step 1: Download

Download [ORemove-Windows-CPU.zip](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest/download/ORemove-Windows-CPU.zip) (about 470 MB).

> If your browser says the file "isn't commonly downloaded", open the download menu, choose **Keep**, then **Keep anyway**.

### Step 2: Extract the zip

1. Right-click **ORemove-Windows-CPU.zip** and choose **Extract All…**
2. Choose where to put it, for example your **Documents** folder, and click **Extract**.

> Don't run ORemove from inside the zip without extracting it first. It won't work properly.

### Step 3: Run ORemove

1. Open the extracted **ORemove** folder.
2. Double-click **ORemove.exe**.
3. If Windows shows **"Windows protected your PC"**, click **More info**, then **Run anyway**.

**Optional:** for a desktop shortcut, right-click **ORemove.exe** and choose **Show more options → Send to → Desktop (create shortcut)**.

Keep everything in the ORemove folder together; the program needs the other files next to it.

---

## 4. Install on a Mac

The Mac edition works on Macs with **Apple Silicon** (M1, M2, M3, M4 or newer) running **macOS 13 Ventura or newer**. It uses the Mac's built-in GPU automatically. Intel Macs are not supported. To check your Mac, open the **Apple menu → About This Mac** and look at **Chip**.

### Step 1: Download and install

1. Download [ORemove-macOS-AppleSilicon.dmg](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest/download/ORemove-macOS-AppleSilicon.dmg).
2. Open the `.dmg` and drag **ORemove** onto the **Applications** folder.
3. Eject the disk image (the ⏏ button next to it in Finder).

### Step 2: Allow it to open (first time only)

ORemove isn't notarized by Apple, so macOS blocks it the first time. You only need to do this once.

**macOS 15 Sequoia and newer:**

1. Open **ORemove** from Applications. macOS says it couldn't verify the app. Click **Done**.
2. Open **System Settings → Privacy & Security**, scroll down, and next to *"ORemove" was blocked*, click **Open Anyway**.
3. Enter your password if asked, then click **Open Anyway** again.

**macOS 13 Ventura and 14 Sonoma:** Control-click (or right-click) **ORemove** in Applications, choose **Open**, then click **Open** in the message.

**If macOS says the app "is damaged and can't be opened":** that's the same block. Open **Terminal** and run this, then open ORemove again:

```bash
xattr -dr com.apple.quarantine /Applications/ORemove.app
```

Continue with [First launch](#5-first-launch).

---

## 5. First launch

1. **Check the device badge.** The window opens straight away, and the badge in the top-right corner shows what ORemove will run on. (The AI engine itself loads when you start your first song, so that song takes a little longer to begin. If the engine finds it can't use your NVIDIA GPU after all, the badge changes to **GPU not used** and a message explains why.)

   | Badge | Meaning |
   |---|---|
   | Your card's name with a **green** dot, e.g. **RTX 4070 SUPER** | The NVIDIA GPU is being used. |
   | **Apple GPU** with a **green** dot (Mac) | The Mac's GPU is being used. |
   | **No NVIDIA GPU** with an **orange** dot (NVIDIA GPU edition) | The GPU can't be used: your NVIDIA driver is probably out of date. See [Installation problems](#9-installation-problems). |
   | **CPU only** with an **orange** dot (CPU edition) | Normal for the CPU edition. |

2. **Try a song.** Drag an audio file onto the window, click **Browse files**, or paste a YouTube link and click **Separate**.
3. **First use of a Roformer model** downloads it once (610–871 MB). This needs an internet connection, and the progress screen says **Downloading model (first use only)…**. After that, the model is reused.
4. **Listen.** When it's done, play the **Vocals** and **Instrumental** cards right in the window.

Your stems are saved to **Music/ORemove** in your user folder, in an **acapellas** and an **instrumentals** folder. See the [README](README.md#quick-start) for how to use the app.

---

## 6. Updating

### NVIDIA GPU edition

Download all three files of the new version and run the installer, exactly as in [section 2](#2-install-the-nvidia-gpu-edition). It upgrades your existing installation and keeps your downloaded models and settings. You don't need to uninstall first.

### Mac

Download the new `.dmg` and drag **ORemove** into **Applications** again, choosing **Replace**. Your models, settings and history are kept.

### CPU edition

1. Download and extract the new zip.
2. **Optional:** to keep your downloaded models, copy the **models** folder from your old ORemove folder into the new one. Copy **settings.json** too to keep your choices.
3. Delete the old folder.

---

## 7. Uninstalling

### NVIDIA GPU edition

1. Open **Settings → Apps → Installed apps** (on Windows 10: **Settings → Apps → Apps & features**).
2. Find **ORemove**, click **⋯** (Windows 10: click the entry), and choose **Uninstall**.

This removes the program, its downloaded models and its settings.

### CPU edition

Delete the ORemove folder.

### Mac

1. Drag **ORemove** from **Applications** to the **Trash**.
2. To also remove its downloaded models, settings and history, open Finder, choose **Go → Go to Folder…**, enter `~/Library/Application Support/ORemove`, and move that folder to the Trash.

**Either edition:** your extracted stems in **Music\ORemove** are never deleted. Remove them yourself if you no longer need them.

---

## 8. Where ORemove keeps its files

| What | NVIDIA GPU edition | CPU edition | Mac |
|---|---|---|---|
| Program | `%LOCALAPPDATA%\Programs\ORemove` | The folder you extracted | `Applications/ORemove` |
| Downloaded models | `models` inside the program folder | `models` inside the ORemove folder | `~/Library/Application Support/ORemove/models` |
| Settings and history | `settings.json` and `history.json` inside the program folder | Same, inside the ORemove folder | `~/Library/Application Support/ORemove` |
| Your stems | `Music\ORemove` (change it in **Settings**) | Same | `Music/ORemove` (change it in **Settings**) |

To open the program folder of the NVIDIA GPU edition, press **Win + R**, type `%LOCALAPPDATA%\Programs\ORemove`, and press **Enter**.

---

## 9. Installation problems

| Problem | Solution |
|---|---|
| **Windows: "The interface could not start… WebView2"** | Install the free [Microsoft Edge WebView2 Runtime](https://go.microsoft.com/fwlink/p/?LinkId=2124703), then start ORemove again. The NVIDIA installer does this for you; the CPU edition relies on it being present (it's built into Windows 11 and most Windows 10 PCs). |
| **Mac: "ORemove Not Opened", "can't be opened" or "is damaged"** | See [Allow it to open](#step-2-allow-it-to-open-first-time-only). |
| **Mac: "You can't open ORemove because this application is not supported on this Mac"** | Your Mac has an Intel chip. Only Apple Silicon Macs (M1 or newer) are supported. |
| **"Windows protected your PC"** | Click **More info**, then **Run anyway**. It appears because ORemove isn't code-signed. |
| **Browser blocks the download** ("isn't commonly downloaded") | In the browser's download list, choose **Keep**, then **Keep anyway**. |
| **Setup says it couldn't download the rest of ORemove** | Check your internet connection and run setup again; it picks up where it left off. If it keeps failing, download every `ORemove-NVIDIA-GPU-Setup` file from the release page (the `.exe` and the `.bin` parts) into one folder and run the `.exe` from there. |
| **The installer asks for a disk or a `.bin` file** ("Setup needs the next disk") | The downloaded parts are missing or in a different folder. Run `ORemove-NVIDIA-GPU-Setup.exe` again so it can download them. |
| **The installer says no NVIDIA graphics card was found** | Your PC has no NVIDIA card Windows can see. Use the [CPU edition](#3-install-the-cpu-edition). |
| **The badge says "No NVIDIA GPU"** on a PC with an NVIDIA card | Update your NVIDIA driver from [nvidia.com/drivers](https://www.nvidia.com/drivers) or the NVIDIA app, restart your PC, then open ORemove again. **Settings → Activity log** in the app shows more. |
| **Antivirus quarantines ORemove** | Some antivirus products flag unsigned apps built with PyInstaller by mistake. Restore the file and add an exception for the ORemove folder, or download it again from the official [Releases page](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest). |
| **"Not enough disk space"** | Free up space on drive C:. The NVIDIA GPU edition needs about 6 GB, plus up to 3 GB for the downloadable models; 12 GB free is recommended. |
| **Badge says "GPU not used"** | The NVIDIA GPU couldn't be used, so ORemove fell back to the CPU. The message under the badge says why (most often an outdated NVIDIA driver). |
| **Separation seems slow on a GPU** | Run the same song twice. The results screen breaks down where the time went: the first run includes one-time downloads and starting the AI engine. On an RTX card, **separating** should take seconds, and **GPU memory** should be in the thousands of MB. |
| **YouTube download fails** for a video that plays fine | ORemove downloads from YouTube with [yt-dlp](https://github.com/yt-dlp/yt-dlp), and YouTube changes its site often, which can break downloads until yt-dlp releases a fix. ORemove 1.5.4 and newer install yt-dlp updates automatically: they check daily, and again whenever a download fails. **Versions 1.5.3 and older never update yt-dlp**, so if you're on one of those, install the latest release first. Open **Settings → YouTube downloader** to see the version and click **Check now** (make sure **Auto-update** is on). If there's no fix yet, try again in a few days; in the meantime, download the audio another way and drop the file into ORemove. Age-restricted and private videos can't be downloaded. |
| **ORemove doesn't start** (CPU edition) | Make sure you extracted the zip (see [section 3](#3-install-the-cpu-edition)) and that all its files are still in the folder. |

Still stuck? [Open an issue](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/issues) with your Windows or macOS version, your graphics card or Mac model, and what you see. Include the text from **Settings → Activity log** if the app opens.

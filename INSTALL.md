# Installing ORemove

This guide walks you through installing **ORemove - AI Powered Vocal Isolator** on Windows, step by step. It takes about 10 minutes, most of it downloading.

**Contents**

1. [Choose your edition](#1-choose-your-edition)
2. [Install the NVIDIA GPU edition](#2-install-the-nvidia-gpu-edition)
3. [Install the CPU edition](#3-install-the-cpu-edition)
4. [First launch](#4-first-launch)
5. [Updating](#5-updating)
6. [Uninstalling](#6-uninstalling)
7. [Where ORemove keeps its files](#7-where-oremove-keeps-its-files)
8. [Installation problems](#8-installation-problems)

---

## 1. Choose your edition

ORemove comes in two editions. Pick the one that matches your graphics card.

| | NVIDIA GPU edition | CPU edition |
|---|---|---|
| **For** | PCs with an **NVIDIA** graphics card (GeForce GTX 900 series or newer) | **Any** Windows PC, including **AMD** and **Intel** graphics |
| **Speed** | Fast: a song takes seconds to about a minute | Slow: a song takes several minutes, up to 30+ with the best models |
| **Download** | About 2.8 GB (an installer in 3 parts) | About 470 MB (one zip file) |
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
- Free disk space: about 6 GB for the NVIDIA GPU edition or 2 GB for the CPU edition, plus about 1.5 GB for the BS-Roformer models (downloaded on first use)
- NVIDIA GPU edition only: an NVIDIA GeForce GTX 900-series or newer card with a current driver

---

## 2. Install the NVIDIA GPU edition

### Step 1: Download all three files

The installer comes in three parts because GitHub limits each file to 2 GB. **You need all three, in the same folder.**

| File | Size |
|---|---|
| [ORemove-NVIDIA-GPU-Setup.exe](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest/download/ORemove-NVIDIA-GPU-Setup.exe) | 2.6 MB |
| [ORemove-NVIDIA-GPU-Setup-1.bin](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest/download/ORemove-NVIDIA-GPU-Setup-1.bin) | 1.9 GB |
| [ORemove-NVIDIA-GPU-Setup-2.bin](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest/download/ORemove-NVIDIA-GPU-Setup-2.bin) | 0.9 GB |

The links always point to the newest version. You can also download the files from the [Releases page](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest).

> If your browser says a file "isn't commonly downloaded", open the download menu, choose **Keep**, then **Keep anyway**. This appears because ORemove isn't code-signed.

Wait until **all three downloads have finished** before continuing. Your browser saves them to your **Downloads** folder by default, which is fine.

### Step 2: Run the installer

1. Open the folder with the three files and double-click **ORemove-NVIDIA-GPU-Setup.exe**.
2. If Windows shows **"Windows protected your PC"**, click **More info**, then **Run anyway**.
3. If the installer says **"No NVIDIA graphics card was found on this PC"**, click **No**. Your PC can't use this edition, so install the [CPU edition](#3-install-the-cpu-edition) instead.

### Step 3: Follow the installer

1. **Welcome:** click **Next**.
2. **License Agreement:** read it, select **I accept the agreement**, and click **Next**.
3. **Destination Location:** keep the default folder and click **Next**. No administrator rights are needed; ORemove installs for your user account only.
4. **Additional Tasks:** tick **Create a desktop shortcut** if you want one, then click **Next**.
5. **Ready to Install:** click **Install**. Installing takes a few minutes.
6. **Finish:** leave **Launch ORemove** ticked and click **Finish**.

You can delete the three downloaded setup files afterwards.

ORemove is now in your **Start Menu**. Continue with [First launch](#4-first-launch).

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

## 4. First launch

1. **Wait for the device badge.** The window opens straight away. The badge in the top-right corner says **Loading engine…** for a few seconds, then shows what ORemove will run on:

   | Badge | Meaning |
   |---|---|
   | Your card's name with a **green** dot, e.g. **RTX 4070 SUPER** | The NVIDIA GPU is being used. |
   | **No NVIDIA GPU** with an **orange** dot (NVIDIA GPU edition) | The GPU can't be used: your NVIDIA driver is probably out of date. See [Installation problems](#8-installation-problems). |
   | **CPU only** with an **orange** dot (CPU edition) | Normal for the CPU edition. |

2. **Try a song.** Paste a YouTube link or click **Browse** to pick an audio file, then click **Extract stems**.
3. **First use of a BS-Roformer model** downloads it once, a few hundred MB. This needs an internet connection, and the status line says **Downloading model (first use only)…**. After that, the model is reused.

Your stems are saved to **Music\ORemove**, in an **acapellas** and an **instrumentals** folder. See the [README](README.md#quick-start) for how to use the app.

---

## 5. Updating

### NVIDIA GPU edition

Download all three files of the new version and run the installer, exactly as in [section 2](#2-install-the-nvidia-gpu-edition). It upgrades your existing installation and keeps your downloaded models and settings. You don't need to uninstall first.

### CPU edition

1. Download and extract the new zip.
2. **Optional:** to keep your downloaded models, copy the **models** folder from your old ORemove folder into the new one. Copy **settings.json** too to keep your choices.
3. Delete the old folder.

---

## 6. Uninstalling

### NVIDIA GPU edition

1. Open **Settings → Apps → Installed apps** (on Windows 10: **Settings → Apps → Apps & features**).
2. Find **ORemove**, click **⋯** (Windows 10: click the entry), and choose **Uninstall**.

This removes the program, its downloaded models and its settings.

### CPU edition

Delete the ORemove folder.

**Either edition:** your extracted stems in **Music\ORemove** are never deleted. Remove them yourself if you no longer need them.

---

## 7. Where ORemove keeps its files

| What | NVIDIA GPU edition | CPU edition |
|---|---|---|
| Program | `%LOCALAPPDATA%\Programs\ORemove` | The folder you extracted |
| Downloaded models | `models` inside the program folder | `models` inside the ORemove folder |
| Settings | `settings.json` inside the program folder | `settings.json` inside the ORemove folder |
| Your stems | `Music\ORemove` (change it in the app with **Change**) | Same |

To open the program folder of the NVIDIA GPU edition, press **Win + R**, type `%LOCALAPPDATA%\Programs\ORemove`, and press **Enter**.

---

## 8. Installation problems

| Problem | Solution |
|---|---|
| **"Windows protected your PC"** | Click **More info**, then **Run anyway**. It appears because ORemove isn't code-signed. |
| **Browser blocks the download** ("isn't commonly downloaded") | In the browser's download list, choose **Keep**, then **Keep anyway**. |
| **The installer asks for a disk or a `.bin` file** ("Setup needs the next disk") | A `.bin` part is missing, still downloading, or in a different folder. Put all three files in the same folder, then click **OK**, or **Browse** to the folder that holds them. |
| **The installer says no NVIDIA graphics card was found** | Your PC has no NVIDIA card Windows can see. Use the [CPU edition](#3-install-the-cpu-edition). |
| **The badge says "No NVIDIA GPU"** on a PC with an NVIDIA card | Update your NVIDIA driver from [nvidia.com/drivers](https://www.nvidia.com/drivers) or the NVIDIA app, restart your PC, then open ORemove again. **Show details** in the app shows more. |
| **Antivirus quarantines ORemove** | Some antivirus products flag unsigned apps built with PyInstaller by mistake. Restore the file and add an exception for the ORemove folder, or download it again from the official [Releases page](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest). |
| **"Not enough disk space"** | Free up space on drive C:. The NVIDIA GPU edition needs about 6 GB, plus about 1.5 GB for the BS-Roformer models. |
| **ORemove doesn't start** (CPU edition) | Make sure you extracted the zip (see [section 3](#3-install-the-cpu-edition)) and that all its files are still in the folder. |

Still stuck? [Open an issue](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/issues) with your Windows version, your graphics card, and what you see. Include the text from **Show details** if the app opens.

# 📱 ADB Py-Transfer

**Version 3.0.3** — *25 July, 2026*

A modern desktop GUI for managing files on Android devices via **ADB**, featuring a dual-pane file manager, wireless ADB pairing/connecting, batch transfers, and real-time progress.
Transfer files in both directions, and handle multiple files with ease.
A powerful cross-platform desktop application for managing file transfers between your PC and Android device.

---

# 🖼️ Screenshots
<img width="356" height="437" alt="Screenshot 2026-08-23 174056" src="https://github.com/user-attachments/assets/0551dbed-0fd3-41b9-9db0-93a7b40a2349" />

<img width="403" height="433" alt="Screenshot 2026-08-23 174141" src="https://github.com/user-attachments/assets/733ef639-2695-49ea-9d08-91f7122dfaf8" />

<img width="1366" height="768" alt="Screenshot 2026-08-23 174216" src="https://github.com/user-attachments/assets/d632956d-414e-4fc7-a9ea-f70583b7f40b" />

---

## ✨ Features

- 🔍 **Device auto-detection** via `adb devices` with model name retrieval
- 📶 **USB & Wireless Support** using ADB Connect / ADB Pair with mDNS discovery (Android 11+)
- 🗂 **Dual-pane file manager** — Local PC (left) / Remote Android (right)
- 📋 **Copy, Cut, Paste, Delete, Rename** on both local and remote sides
- 🚀 **Batch transfer** for optimized multi-file transfers with configurable files‑at‑once speed (1–9999)
- 📊 **Real‑time progress** with percentage and current file information and cancel support
- 👻 **Show hidden files** toggle for local pane
- 🧾 **Optional ADB logging** to `adb_log.txt`
- ♻️ **Recycle Bin support** for local deletions (optional via `send2trash`)
- ⌨️ **Keyboard shortcuts** for rapid clipboard and file operations
- 🧵 **Threaded transfers** keep the UI responsive
- 📂 **Smart Sorting** – Directories always appear before files; size column sorts numerically
- 🎨 **Modern UI** – Built with PyQt5 for a clean, responsive experience

---

## 📦 Requirements

- **Windows** (tested on Windows 10/11) – may work on Linux/macOS with minor adjustments, but designed for Windows.
- **Python 3.8+ (if running from source; Tested on: 3.13.14✅)**
- **PyQt5** – GUI framework
- **ADB (Android Debug Bridge)** – installed and available in `PATH`
- **send2trash** *(optional)* – send local deleted files to Recycle Bin instead of permanent delete

### Install Python dependencies

```bash
pip install PyQt5
# Optional but recommended:
pip install send2trash
```

### Install ADB

- **Windows:** Download [Platform Tools](https://developer.android.com/tools/releases/platform-tools) and add to PATH
- **Linux:** `sudo apt install adb`
- **macOS:** `brew install android-platform-tools`

---

## 🚀 Installation

1. **Clone this repository:**
   ```bash
   git clone https://github.com/yourusername/adb-py-transfer.git
   cd adb-py-transfer
   ```

2. **Install dependencies:**
   ```bash
   pip install PyQt5
   pip install send2trash   # optional
   ```

3. **Run the program:**
   ```bash
   python "ADB Py-Transfer V.3.0.3.py"
   ```

---

## 🖥️ Usage

1. Connect your Android device via **USB** or use **Wireless ADB**.
2. Launch the application.  
   The **Device Selector** window will automatically search for ADB devices.
3. Select a device from the list and click **Continue**.
4. The **File Manager** opens with two panes:
   - **Left pane:** Local PC
   - **Right pane:** Remote Android device
   - Middle buttons: `Copy ->`, `Cut ->`, `<- Copy`, `<- Cut`
5. Adjust the **Transfer Speed (Files at once)** spinbox to control batch size (**Note:** Transferring files within a single folder in a single batch is fastest).
6. Use **right‑click context menus** or **keyboard shortcuts** for file operations.

### Wireless Device Setup

- Click **Add Wireless Device** in the Device Selector.
- The dialog has two tabs:
  - **ADB Connect** – scan for connectable devices or enter `IP:PORT` manually.
  - **ADB Pair** – scan for pairable devices or enter `IP:PORT` + pairing code.
- After successful pairing, return to the **ADB Connect** tab and connect.

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl + C` | Copy selected item(s) |
| `Ctrl + X` | Cut selected item(s) |
| `Ctrl + V` | Paste clipboard item(s) |
| `Delete`  | Delete selected item(s) |
| `F2`      | Rename selected item |
| `Enter` / `Return` | Continue in Device Selector |

---

## ⚙️ Configuration

### Transfer Speed (Batch Size)

The spin box at the top of the file manager controls how many files are transferred **simultaneously** in a single ADB command. Higher values can improve speed for many small files, while lower values are better for large files.

### ADB Logging

Enable the checkbox in the device selector to write all ADB commands and outputs to `adb_log.txt` in the application directory. Useful for troubleshooting.

---

## 🧠 How It Works

1. The **Device Selector** scans `adb devices` every second (or every 5 seconds when a device is connected).
2. The **File Manager** reads remote directories using `adb shell ls -laL`.
3. File transfers use `adb push` / `adb pull`, with optional batch mode.
4. A background thread polls the destination size to update the progress bar.
5. Cut operations delete the source only after successful transfer.
6. Local deletions use Recycle Bin if `send2trash` is installed; otherwise permanent delete.
7. The left pane shows your local PC’s file system, the right pane shows the Android device’s file system (starting at `/sdcard/`).

---

## 🛡️ Known Limitations / Notes

- Wireless pairing requires **Android 11+** and both PC and device on the same network.
- Remote file listing may fail on directories with special characters or insufficient permissions
- Some protected directories (e.g., `/data`) cannot be accessed without root.
- If ADB is not found, make sure it is added to your system `PATH`.
- The progress percentage for bulk transfers is calculated from total file sizes and may fluctuate slightly.

---

## 🧪 Troubleshooting

### "ADB not found in PATH"
- Install Android Platform Tools
- Add the folder containing `adb.exe` to your system PATH
- Restart the application

### "Permission denied" on Android
- Ensure the device is unlocked and USB Debugging is enabled
- Check app permissions on your Android device
- Try navigating to a different directory (e.g., `/sdcard/Download/`)

### Slow transfers
- Increase the **Batch Size** for bulk transfers
- Use USB 3.0 cable and port if available
- For wireless, ensure strong Wi-Fi signal

### "Device offline" or "unauthorized"
- Reconnect the USB cable
- Check the RSA fingerprint prompt on your device and accept it
- Restart ADB server: `adb kill-server` then `adb start-server`

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0** – see the [LICENSE](LICENSE) file for details.

---

## 🤝 Contributing

Feel free to fork this repository and submit pull requests to improve features, fix bugs, or enhance performance.

---

## 📧 Contact

**Author:** Ayan Khan  
**GitHub:** [ayankhanakaak](https://github.com/ayankhanakaak)  
**Email:** [ayankhantnp786@gmail.com](mailto:ayankhantnp786@gmail.com)

---

⭐ If you find this tool useful, consider giving the repository a star!

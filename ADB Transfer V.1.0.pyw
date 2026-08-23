'''
App Name: ADB Py-Transfer
Version: 1.0
Completion Date: 25 July, 2026
'''

import sys
import os
import subprocess
import re
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QListWidget, QLabel, QMessageBox,
                             QMainWindow, QTreeView, QFileSystemModel, QSplitter,
                             QProgressDialog, QAbstractItemView, QHeaderView, QDesktopWidget,
                             QListWidgetItem, QCheckBox, QStyle, QFileIconProvider)
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QIcon
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDir, QModelIndex, QSortFilterProxyModel, QFileInfo
from datetime import datetime

class AdbLogger:
    ENABLED = False
    
    @staticmethod
    def log(command, output):
        if not AdbLogger.ENABLED:
            return
        try:
            with open("adb_log.txt", "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}]\nCommand: {command}\nOutput:\n{output.strip()}\n{'-'*40}\n")
        except Exception:
            pass

class DirectoryFirstProxyModel(QSortFilterProxyModel):
    def lessThan(self, left_index, right_index):
        source_model = self.sourceModel()
        left_item = source_model.itemFromIndex(left_index.siblingAtColumn(0))
        right_item = source_model.itemFromIndex(right_index.siblingAtColumn(0))
        
        left_name = left_item.text() if left_item else ""
        right_name = right_item.text() if right_item else ""
        
        # Keep ".." always pinned at the top
        if left_name == "..": return self.sortOrder() == Qt.AscendingOrder
        if right_name == "..": return self.sortOrder() == Qt.DescendingOrder

        left_is_dir = left_item.data(Qt.UserRole) == "dir" if left_item else False
        right_is_dir = right_item.data(Qt.UserRole) == "dir" if right_item else False

        # Group folders above files
        if left_is_dir != right_is_dir:
            return left_is_dir if self.sortOrder() == Qt.AscendingOrder else right_is_dir

        # Correctly sort Size column (index 1) numerically instead of alphabetically
        if left_index.column() == 1:
            l_text = source_model.data(left_index)
            r_text = source_model.data(right_index)
            l_val = int(l_text) if l_text and l_text.isdigit() else -1
            r_val = int(r_text) if r_text and r_text.isdigit() else -1
            return l_val < r_val

        return super().lessThan(left_index, right_index)

def get_android_item_icon(file_name, is_dir=False):
    provider = QFileIconProvider()

    if is_dir:
        return provider.icon(QFileIconProvider.Folder)

    return provider.icon(QFileInfo(file_name))

def get_startupinfo():
    """Hides the console window for subprocesses on Windows."""
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo

class SizePollingThread(QThread):
    progress = pyqtSignal(int, str)
    debug = pyqtSignal(str, str)

    def __init__(self, check_func, total_size, file_index, total_files, label, interval_ms=1000):
        super().__init__()
        self.check_func = check_func
        self.total_size = total_size
        self.file_index = file_index
        self.total_files = total_files
        self.label = label
        self.interval_ms = interval_ms
        self.running = True
        self.last_percent = -1

    def run(self):
        while self.running:
            try:
                current_size = self.check_func()
                if self.total_size > 0 and self.total_files > 0:
                    file_percent = int((current_size * 100) / self.total_size)
                    file_percent = max(0, min(file_percent, 100))

                    if file_percent != self.last_percent:
                        self.last_percent = file_percent
                        overall_percent = int(((self.file_index + (file_percent / 100.0)) / self.total_files) * 100)
                        overall_percent = max(0, min(overall_percent, 100))
                        self.progress.emit(overall_percent, self.label)
            except Exception as e:
                self.debug.emit("Size polling error", str(e))

            self.msleep(self.interval_ms)

    def stop(self):
        self.running = False
        self.quit()
        self.wait()


class TransferWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, device_id, direction, transfers):
        super().__init__()
        self.device_id = device_id
        self.direction = direction
        self.transfers = transfers
        self.poller = None
        self.process = None

    def cancel(self):
        self.requestInterruption()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass

    def stop_poller(self):
        if self.poller:
            self.poller.stop()
            self.poller = None

    def run_adb_size(self, remote_path):
        remote_cmd = f'stat -c %s "{remote_path}"'
        cmd = ['adb', '-s', self.device_id, 'shell', remote_cmd]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            startupinfo=get_startupinfo()
        )

        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()

        AdbLogger.log(" ".join(cmd), error or output)

        if result.returncode != 0:
            return 0

        try:
            return int(output.splitlines()[-1].strip())
        except Exception:
            AdbLogger.log(" ".join(cmd), f"Could not parse stat output: {output}")
            return 0

    def run_transfer_command(self, cmd):
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            startupinfo=get_startupinfo()
        )

        output_lines = []
        while True:
            if self.isInterruptionRequested():
                self.cancel()
                raise RuntimeError("Transfer cancelled.")

            line = self.process.stdout.readline()
            if line:
                output_lines.append(line.rstrip())
            elif self.process.poll() is not None:
                break

        returncode = self.process.wait()
        output = "\n".join(output_lines)
        AdbLogger.log(" ".join(cmd), output)

        if returncode != 0:
            raise RuntimeError(output or f"adb transfer failed with exit code {returncode}")

    def run(self):
        try:
            total_files = len(self.transfers)
            if total_files == 0:
                self.finished.emit(True, "No files to transfer.")
                return

            for idx, (local_path, remote_path) in enumerate(self.transfers):
                file_number = idx + 1
                file_counter = f"({file_number}/{total_files})"
                name = os.path.basename(local_path if self.direction == 'push' else remote_path)

                if self.direction == 'push':
                    total_size = os.path.getsize(local_path)

                    def check_destination_size(path=remote_path):
                        return self.run_adb_size(path)

                    label = f"{file_counter} Pushing:\n{name}"
                    self.progress.emit(int((idx / total_files) * 100), label)

                    self.poller = SizePollingThread(check_destination_size, total_size, idx,total_files, label)
                    self.poller.progress.connect(self.progress)
                    self.poller.debug.connect(AdbLogger.log)
                    self.poller.start()

                    cmd = ['adb', '-s', self.device_id, 'push', local_path, remote_path]
                    AdbLogger.log(" ".join(cmd), "Transfer Started")
                    self.run_transfer_command(cmd)
                    self.stop_poller()

                else:
                    total_size = self.run_adb_size(remote_path)

                    def check_destination_size(path=local_path):
                        return os.path.getsize(path) if os.path.exists(path) else 0

                    label = f"{file_counter} Pulling:\n{name}"
                    self.progress.emit(int((idx / total_files) * 100), label)

                    self.poller = SizePollingThread(check_destination_size, total_size, idx, total_files, label)
                    self.poller.progress.connect(self.progress)
                    self.poller.debug.connect(AdbLogger.log)
                    self.poller.start()

                    cmd = ['adb', '-s', self.device_id, 'pull', remote_path, local_path]
                    AdbLogger.log(" ".join(cmd), "Transfer Started")
                    self.run_transfer_command(cmd)
                    self.stop_poller()

                self.progress.emit(int((file_number / total_files) * 100), f"{file_counter} Completed:\n{name}")

            self.progress.emit(100, f"{self.direction.capitalize()} completed.")
            self.finished.emit(True, "Transfer(s) completed successfully.")

        except Exception as e:
            self.stop_poller()
            AdbLogger.log("Transfer Error", str(e))
            self.finished.emit(False, str(e))


class FileManager(QMainWindow):
    closed = pyqtSignal()

    def __init__(self, device_id, device_name):
        super().__init__()
        self.device_id = device_id
        self.device_name = device_name
        self.current_remote_path = "/sdcard/"
        self.initUI()
        self.load_remote_directory(self.current_remote_path)

    def initUI(self):
        self.setWindowTitle(f"ADB File Commander - {self.device_name}")
        self.resize(1000, 600)
        self.center()

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # Left Pane - Local PC
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        
        local_header_layout = QHBoxLayout()
        local_header_layout.addWidget(QLabel("Local PC"))
        
        # 3. Toggle for hidden files
        self.chk_hidden = QCheckBox("Show Hidden Files")
        self.chk_hidden.stateChanged.connect(self.toggle_hidden_files)
        local_header_layout.addWidget(self.chk_hidden, alignment=Qt.AlignRight)
        left_layout.addLayout(local_header_layout)
        
        self.local_model = QFileSystemModel()
        self.local_model.setRootPath('') 
        self.local_model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs) # Default: No hidden
        
        self.local_tree = QTreeView()
        self.local_tree.setModel(self.local_model)
        # 2. Empty root index to show drives (C:, D:, etc.)
        self.local_tree.setRootIndex(self.local_model.index(''))
        self.local_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.local_tree.setSortingEnabled(True)
        self.local_tree.sortByColumn(0, Qt.AscendingOrder)
        left_layout.addWidget(self.local_tree)

        # Center Buttons
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        self.btn_push = QPushButton("Push ->")
        self.btn_pull = QPushButton("<- Pull")
        self.btn_push.clicked.connect(self.push_file)
        self.btn_pull.clicked.connect(self.pull_file)
        
        center_layout.addStretch()
        center_layout.addWidget(self.btn_push)
        center_layout.addWidget(self.btn_pull)
        center_layout.addStretch()

        # Right Pane - Remote Android
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        self.remote_path_label = QLabel(f"Remote Device: {self.current_remote_path}")
        right_layout.addWidget(self.remote_path_label)

        self.remote_model = QStandardItemModel()
        self.remote_model.setHorizontalHeaderLabels(['Name', 'Size', 'Date', 'Permissions'])
        
        # Apply the Custom Proxy Model for intelligent sorting
        self.proxy_model = DirectoryFirstProxyModel()
        self.proxy_model.setSourceModel(self.remote_model)
        
        self.remote_tree = QTreeView()
        self.remote_tree.setModel(self.proxy_model)
        self.remote_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.remote_tree.doubleClicked.connect(self.remote_double_clicked)
        self.remote_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.remote_tree.setSortingEnabled(True)
        self.remote_tree.sortByColumn(0, Qt.AscendingOrder)
        
        self.local_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.remote_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        right_layout.addWidget(self.remote_tree)

        splitter.addWidget(left_widget)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([450, 100, 450])

    def center(self):
        qr = self.frameGeometry()
        cp = QDesktopWidget().availableGeometry().center()
        qr.moveCenter(cp)
        self.move(qr.topLeft())

    def toggle_hidden_files(self, state):
        base_filter = QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs
        if state == Qt.Checked:
            self.local_model.setFilter(base_filter | QDir.Hidden | QDir.System)
        else:
            self.local_model.setFilter(base_filter)

    def run_adb_shell(self, cmd_string):
        cmd = ['adb', '-s', self.device_id, 'shell', cmd_string]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=get_startupinfo())
            AdbLogger.log(" ".join(cmd), result.stdout)
            return result.stdout, result.returncode
        except Exception as e:
            AdbLogger.log(" ".join(cmd), f"ERROR: {str(e)}")
            return "", -1

    def load_remote_directory(self, path):
        self.remote_model.removeRows(0, self.remote_model.rowCount())
        self.current_remote_path = path
        self.remote_path_label.setText(f"Remote Device: {self.current_remote_path}")

        stdout, code = self.run_adb_shell(f'ls -laL "{path}"')
        
        if code != 0 or "Permission denied" in stdout:
            QMessageBox.warning(self, "Error", f"Cannot read directory: {path}\nPermission denied.")
            if path != "/sdcard/":
                self.load_remote_directory("/sdcard/")
            return

        if path != "/":
            up_item = QStandardItem("..")
            up_item.setData("dir", Qt.UserRole)
            up_item.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
            self.remote_model.appendRow([up_item, QStandardItem(""), QStandardItem(""), QStandardItem("")])

        lines = stdout.strip().split('\n')
        for line in lines:
            if not line or line.startswith('total '): continue
            
            parts = line.split(maxsplit=7)
            if len(parts) < 8: continue
            
            perms, links, owner, group, size, date, time, name = parts
            
            if name in ('.', '..'): continue

            is_dir = perms.startswith('d')
            
            name_item = QStandardItem(name)
            name_item.setData("dir" if is_dir else "file", Qt.UserRole)
            
            name_item.setIcon(get_android_item_icon(name, is_dir))
            
            size_item = QStandardItem(size if not is_dir else "")
            date_item = QStandardItem(f"{date} {time}")
            perm_item = QStandardItem(perms)

            self.remote_model.appendRow([name_item, size_item, date_item, perm_item])

    def remote_double_clicked(self, index):
        # Map the clicked index through the Proxy Model back to the true Source Model
        source_index = self.proxy_model.mapToSource(index)
        item = self.remote_model.item(source_index.row(), 0)
        item_type = item.data(Qt.UserRole)
        name = item.text()

        if name == "..":
            new_path = os.path.dirname(self.current_remote_path.rstrip('/'))
            if not new_path: new_path = "/"
            if not new_path.endswith('/'): new_path += "/"
            self.load_remote_directory(new_path)
        elif item_type == "dir":
            new_path = f"{self.current_remote_path}{name}/"
            self.load_remote_directory(new_path)

    def get_selected_local_paths(self):
        paths = []
        for index in self.local_tree.selectionModel().selectedRows():
            if index.isValid():
                paths.append(self.local_model.filePath(index))
        return paths

    def get_selected_remote_names(self):
        names = []
        for index in self.remote_tree.selectionModel().selectedRows():
            if index.isValid():
                source_index = self.proxy_model.mapToSource(index)
                names.append(self.remote_model.item(source_index.row(), 0).text())
        return names

    def center_transfer_dialog(self):
        if not hasattr(self, "progress_dialog") or self.progress_dialog is None:
            return

        parent_rect = self.geometry()
        dialog_rect = self.progress_dialog.frameGeometry()
        dialog_rect.moveCenter(parent_rect.center())
        self.progress_dialog.move(dialog_rect.topLeft())

    def update_transfer_progress(self, value, text):
        if not hasattr(self, "progress_dialog") or self.progress_dialog is None:
            return

        self.progress_dialog.setValue(value)
        self.progress_dialog.setLabelText(text)
        QApplication.processEvents()
        self.center_transfer_dialog()

    def start_transfer(self, direction, transfers):
        self.progress_dialog = QProgressDialog(f"{direction.capitalize()}ing...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowTitle("File Transfer")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setAutoClose(False)
        self.progress_dialog.setAutoReset(False)
        self.progress_dialog.setMinimumDuration(0)

        self.worker = TransferWorker(self.device_id, direction, transfers)
        self.worker.progress.connect(self.update_transfer_progress)
        self.worker.finished.connect(self.on_transfer_finished)
        self.progress_dialog.canceled.connect(self.worker.cancel)

        self.progress_dialog.show()
        self.center_transfer_dialog()
        self.worker.start()
        self.progress_dialog.show()

    def push_file(self):
        local_paths = self.get_selected_local_paths()
        if not local_paths:
            QMessageBox.information(self, "Info", "Select valid files from the Local PC to push.")
            return
            
        transfers = []
        for path in local_paths:
            if os.path.isfile(path):
                remote_path = f"{self.current_remote_path}{os.path.basename(path)}"
                transfers.append((path, remote_path))
                
        if transfers:
            self.start_transfer('push', transfers)

    def pull_file(self):
        remote_names = self.get_selected_remote_names()
        if not remote_names or ".." in remote_names:
            QMessageBox.information(self, "Info", "Select valid files from the Remote Device to pull.")
            return
            
        index = self.local_tree.currentIndex()
        if not index.isValid() or self.local_model.filePath(index) == "":
            QMessageBox.information(self, "Info", "Select a valid destination directory on the Local PC.")
            return

        local_dir = self.local_model.filePath(index) if self.local_model.isDir(index) else os.path.dirname(self.local_model.filePath(index))

        transfers = []
        for name in remote_names:
            remote_path = f"{self.current_remote_path}{name}"
            local_path = os.path.join(local_dir, name)
            transfers.append((local_path, remote_path))
        
        if transfers:
            self.start_transfer('pull', transfers)

    def on_transfer_finished(self, success, message):
        if hasattr(self, "worker") and self.worker is not None:
            try:
                self.worker.progress.disconnect(self.update_transfer_progress)
            except Exception:
                pass

        if hasattr(self, 'progress_dialog') and self.progress_dialog is not None:
            self.progress_dialog.close()
            self.progress_dialog = None

        if success:
            self.load_remote_directory(self.current_remote_path)
        else:
            QMessageBox.critical(self, "Error", message)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


class DeviceSelector(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle('ADB Device Selector V.1.0')
        self.resize(350, 400)
        
        layout = QVBoxLayout()
        
        self.label = QLabel("Searching for ADB devices...")
        layout.addWidget(self.label)
        
        self.device_list = QListWidget()
        self.device_list.itemSelectionChanged.connect(self.check_selection)
        self.device_list.itemDoubleClicked.connect(self.launch_file_manager)
        layout.addWidget(self.device_list)
        
        btn_layout = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_devices)
        
        self.btn_continue = QPushButton("Continue")
        self.btn_continue.setEnabled(False)
        self.btn_continue.clicked.connect(self.launch_file_manager)
        
        btn_layout.addWidget(self.btn_refresh)
        btn_layout.addWidget(self.btn_continue)
        
        self.chk_log = QCheckBox("Enable ADB Logging (adb_log.txt)")
        self.chk_log.stateChanged.connect(self.toggle_logging)
        
        layout.addLayout(btn_layout)
        layout.addWidget(self.chk_log)
        self.setLayout(layout)
        
        self.refresh_devices()

    def toggle_logging(self, state):
        AdbLogger.ENABLED = (state == Qt.Checked)

    def refresh_devices(self):
        self.device_list.clear()
        self.btn_continue.setEnabled(False)
        self.label.setText("Refreshing...")
        QApplication.processEvents()
        
        try:
            cmd = ['adb', 'devices']
            result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=get_startupinfo())
            AdbLogger.log(" ".join(cmd), result.stdout)
            lines = result.stdout.strip().split('\n')
            
            devices_found = 0
            for line in lines[1:]:
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) == 2:
                        dev_id, status = parts
                        
                        # 1. Fetch friendly name if authorized
                        if status == 'device':
                            model_cmd = ['adb', '-s', dev_id, 'shell', 'getprop', 'ro.product.model']
                            try:
                                model_result = subprocess.run(model_cmd, capture_output=True, text=True, startupinfo=get_startupinfo(), timeout=2)
                                AdbLogger.log(" ".join(model_cmd), model_result.stdout)
                                model_name = model_result.stdout.strip()
                            except Exception as e:
                                AdbLogger.log(" ".join(model_cmd), f"ERROR: {str(e)}")
                                model_name = ""
                            
                            display_name = f"{model_name} [{dev_id}]" if model_name else f"Unknown Device [{dev_id}]"
                        else:
                            display_name = f"[{dev_id}] ({status})"
                            
                        item = QListWidgetItem(display_name)
                        item.setData(Qt.UserRole, dev_id)
                        item.setData(Qt.UserRole + 1, status)
                        self.device_list.addItem(item)
                        devices_found += 1
                        
            if devices_found == 0:
                self.label.setText("No devices found. Ensure USB debugging is ON.")
            else:
                self.label.setText(f"Found {devices_found} device(s).")
                
        except FileNotFoundError as e:
            AdbLogger.log("adb devices", f"ERROR: ADB not found in PATH. {str(e)}")
            self.label.setText("ADB not found. Is it added to your system PATH?")
            QMessageBox.critical(self, "ADB Error", "Could not find ADB executable.\nPlease install ADB and add it to PATH.")

    def check_selection(self):
        items = self.device_list.selectedItems()
        if items and items[0].data(Qt.UserRole + 1) == "device":
            self.btn_continue.setEnabled(True)
        else:
            self.btn_continue.setEnabled(False)

    def launch_file_manager(self):
        selected_item = self.device_list.selectedItems()[0]
        device_id = selected_item.data(Qt.UserRole)
        device_name = selected_item.text().split(' [')[0] # Extract friendly name for title
        
        self.hide()
        self.fm = FileManager(device_id, device_name)
        self.fm.closed.connect(self.show)
        self.fm.show()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    selector = DeviceSelector()
    selector.show()
    sys.exit(app.exec_())
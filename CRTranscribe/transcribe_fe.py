# transcribe_fe.py

import sys
from io import BytesIO
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QTabWidget, QLineEdit, QTextEdit,
                              QMessageBox, QFileDialog, QStatusBar)
from PySide6.QtCore import Signal, QStandardPaths

from WrapSideSix import (WSGridLayoutHandler, WSGridRecord, WSGridPosition,
                       WSMessageDialog, WSProgressDialog, DropdownItem, WSToolbarIcon)
from WrapConfig import RuntimeConfig, SecretsManager

from WrapAV import AudioTranscriber, MediaFileAnalyzer

from typing import Optional
from dataclasses import asdict

from WrapSideSix.icons import icons_mat_des
icons_mat_des.qInitResources()

def get_ffmpeg_paths():
    # Determine the base directory of the executable or script
    exe_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    ffmpeg_path = exe_dir / "ffmpeg"
    ffprobe_path = exe_dir / "ffprobe"

    if sys.platform == "win32":  # Add .exe extension for Windows
        ffmpeg_path = ffmpeg_path.with_suffix('.exe')
        ffprobe_path = ffprobe_path.with_suffix('.exe')

    return ffmpeg_path, ffprobe_path


def format_time(seconds):
    """Helper function to format seconds into MM:SS."""
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    return f"{minutes:02}:{seconds:02}"

@dataclass
class TranscriptWindowData:
    process: str
    mp3_file: Optional[BytesIO] = None
    file_name: Optional[str] = None
    transcript: Optional[str] = None
    time_stamps: Optional[str] = None
    file_info: Optional[str] = None


class CapTranscriptWindow(QMainWindow):
    emit_data = Signal(TranscriptWindowData)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChatRecall Transcribe")
        self.setMinimumWidth(800)

        # ffmpeg, ffprobe = get_ffmpeg_paths()

        secrets_manager =SecretsManager()
        secrets_manager.load_secrets()
        self.api_key = secrets_manager.get_secret("OPENAI_API_KEY")

        self.run_time = RuntimeConfig()
        self.transcriber = AudioTranscriber(api_key=self.api_key)
        self.media_info = None
        self.transcription = None

        self.executor = None
        self.error_message = None
        self.imported = False

        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.status_bar = QStatusBar()

        self.tab_widget = QTabWidget()
        self.info_text_edit = QTextEdit()  # Info tab
        self.transcript_text_edit = QTextEdit()  # Transcript tab
        self.transcript_with_timestamps_text_edit = QTextEdit()
        self.tab_widget.addTab(self.info_text_edit, "Info")
        self.tab_widget.addTab(self.transcript_text_edit, "Transcript")
        self.tab_widget.addTab(self.transcript_with_timestamps_text_edit, "Transcript with Timestamps")

        self.transcript = None
        self.header_grid_widget = None

        # Form fields
        self.grid_layout_handler = WSGridLayoutHandler()
        self.header_grid = WSGridLayoutHandler()
        self.toolbar = WSToolbarIcon('toolbar')
        self.mp3_input = QLineEdit()
        self.text_edit_box = QTextEdit()

        self.init_ui()
        self.init_toolbar()
        self.init_status_bar()
        self.connect_signals()

    def init_ui(self):
        header_grid_widgets = [
            WSGridRecord(widget=QLabel("File:"), position=WSGridPosition(row=0, column=0), col_stretch=0),
            WSGridRecord(widget=self.mp3_input, position=WSGridPosition(row=0, column=1), col_stretch=10),
            ]

        self.header_grid.add_widget_records(header_grid_widgets)
        self.header_grid_widget = self.header_grid.as_widget()

        main_grid_widgets = [
            WSGridRecord(widget=self.header_grid_widget, position=WSGridPosition(row=0, column=0)),
            WSGridRecord(widget=self.tab_widget, position=WSGridPosition(row=1, column=0))
            ]

        self.grid_layout_handler.add_widget_records(main_grid_widgets)
        self.setCentralWidget(self.grid_layout_handler.as_widget())

    def init_toolbar(self):
        self.addToolBar(self.toolbar)
        self.toolbar.clear_toolbar()

        # Default icons for toolbar actions
        load_file_icon = ":/icons/mat_des/file_open_24dp.png"
        transcribe_file_icon = ":/icons/mat_des/play_arrow_24dp.png"
        reset_icon_path = ":/icons/mat_des/loop_24dp.png"
        exit_icon_path = ":/icons/mat_des/exit_to_app_24dp.png"
        copy_icon_path = ":/icons/mat_des/content_copy_24dp.png"
        save_icon_path = ":/icons/mat_des/save_24dp.png"

        self.toolbar.add_action_to_toolbar(
            "load",
            "Load",
            "Load file",
            self.load_file_info,
            load_file_icon)

        self.toolbar.add_action_to_toolbar(
            "transcribe",
            "Transcribe",
            "Transcribe file",
            self.transcribe_time_stamps,
            transcribe_file_icon)

         # Define dropdown menu with icons
        dropdown_copy = [
            DropdownItem("Copy Transcript", self.copy_transcript),
            DropdownItem("Copy Timestamps", self.copy_timestamps),
        ]
        self.toolbar.update_dropdown_menu(
            name="copy_menu",
            icon=copy_icon_path,
            dropdown_definitions=dropdown_copy)

        dropdown_save = [
            DropdownItem("Save Transcript", self.save_transcript),
            DropdownItem("Save Timestamps", self.save_timestamps),
        ]
        self.toolbar.update_dropdown_menu(
            name="save_menu",
            icon=save_icon_path,
            dropdown_definitions=dropdown_save)

        self.toolbar.add_action_to_toolbar(
            "reset",
            "Reset",
            "Reset screen",
            self.reset_fields,
            reset_icon_path)

        self.toolbar.add_action_to_toolbar(
            "exit",
            "Exit",
            "Exit Program",
            self.close,
            exit_icon_path)

    def init_status_bar(self):
        self.setStatusBar(self.status_bar)
        self.update_status_bar()

    def connect_signals(self):
        self.mp3_input.editingFinished.connect(self.load_info)

    # Status bar methods
    def update_status_bar(self, message="Status bar", duration=5000):
        self.statusBar().showMessage(message, duration)
        QApplication.processEvents()

    def clear_status_bar(self):
        self.statusBar().clearMessage()

    def load_file_info(self):
        filename = (QFileDialog.getOpenFileName(self, "Load Audio File",
                                                str(self.run_time.home_dir),
                                                "Files (Audio Files (*.flac *.mp3 *.mp4 *.mpeg *.mpga *.m4a *.ogg *.wav *.webm);; All Files (*)")[0])

        if filename is None or filename == '':
            return

        self.mp3_input.setText(filename)
        self.load_info()

    def load_info(self):
        filename = self.mp3_input.text()
        self.text_edit_box.clear()

        def task():
            try:
                # self.transcriber.load_file_to_memory(filename)
                audio_file_path = Path(filename)
                analyzer = MediaFileAnalyzer(audio_file_path)
                self.media_info = analyzer.get_all_info()
                self.error_message = None
            except Exception as e:
                self.error_message = str(e)
                # print(self.error_message)
                self.text_edit_box.append(f"Error loading {filename} to memory: {self.error_message}")

        self.executor = WSProgressDialog(task, title="Loading File to Memory")
        self.executor.exec_()

        if self.error_message:
            self.show_error_message()
        else:
            # Convert the dataclass to a string format
            media_info_dict = asdict(self.media_info)
            formatted_info = "\n".join(f"{key}: {value}" for key, value in media_info_dict.items())
            # self.text_edit_box.setText(formatted_info)
            self.info_text_edit.setText(formatted_info)

        self.imported = True

    def transcribe(self):
        if not self.api_key:
            dialog = WSMessageDialog("Info", 'API Key required to transcribe')
            dialog.confirm()
            return

        filename = self.mp3_input.text()
        self.text_edit_box.clear()

        def task():
            try:
                self.transcription = self.transcriber.transcribe_audio(Path(filename))
            except Exception as e:
                self.error_message = str(e)
                # print(str(e))
                if "Invalid API key provided" in self.error_message:
                    pass  # Handle the error after exec
                else:
                    self.text_edit_box.append(f"Error transcribing {filename}: {self.error_message}")
                    # print(self.error_message)

        self.executor = WSProgressDialog(task, title="Import Progress")
        self.executor.exec_()

        if self.error_message:
            self.show_error_message()

        self.mp3_input.setText(filename)
        if isinstance(self.transcription, dict):
            # Format the transcription dictionary into a string to display
            formatted_info = "\n".join(f"{key}: {value}" for key, value in self.transcription.items())
            self.text_edit_box.setText(formatted_info)
        else:
            self.text_edit_box.setText(str(self.transcription))  # In case transcription is a string

        self.imported = True

    def transcribe_time_stamps(self):
        if not self.api_key:
            dialog = WSMessageDialog("Info", 'API Key required to transcribe')
            dialog.confirm()
            return

        filename = self.mp3_input.text()
        self.text_edit_box.clear()

        def task():
            try:
                self.transcription = self.transcriber.transcribe_audio(Path(filename), time_stamps=True)
            except Exception as e:
                self.error_message = str(e)
                # print(str(e))
                if "Invalid API key provided" in self.error_message:
                    pass  # Handle the error after exec
                else:
                    self.text_edit_box.append(f"Error transcribing {filename}: {self.error_message}")
                    # print(self.error_message)

        self.executor = WSProgressDialog(task, title="Import Progress")
        self.executor.exec_()

        if self.error_message:
            self.show_error_message()

        self.mp3_input.setText(filename)

        if isinstance(self.transcription, dict):
            # Start building the formatted string for display
            formatted_info = "### Transcription Text ###\n\n"
            formatted_info += self.transcription.get('text', '') + "\n\n"

            # Segments section
            formatted_info += "### Segments ###\n"
            segments = self.transcription.get('segments', [])
            formatted_segment_text = ''

            for segment in segments:
                start_time = format_time(segment['start'])
                end_time = format_time(segment['end'])
                segment_text = segment['text']
                formatted_info += f"\n{start_time} - {end_time}: {segment_text}\n"
                formatted_segment_text += f"\n{start_time} - {end_time}: {segment_text}\n"

            # Display the formatted info in the QTextEdit
            self.text_edit_box.setText(formatted_info)

            self.transcript_text_edit.setText(self.transcription.get('text', ''))
            self.transcript_with_timestamps_text_edit.setText(formatted_segment_text)

        else:
            self.text_edit_box.setText(str(self.transcription))  # In case transcription is a string

        self.imported = True

    def show_error_message(self):
        if self.error_message:  # Only show if there's an error message
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Icon.Critical)
            msg_box.setText(self.error_message)  # Display the actual error message
            msg_box.setWindowTitle("Error")
            msg_box.exec()

    def reset_fields(self, extract=False):
        self.mp3_input.clear()
        self.text_edit_box.clear()
        self.info_text_edit.clear()
        self.transcript_text_edit.clear()
        self.transcript_with_timestamps_text_edit.clear()

        del self.transcriber
        self.transcriber = AudioTranscriber(self.api_key)
        if not extract:
            self.update_status_bar("Capture reset")

    def save_transcript(self):
        self.save_text(self.transcript_text_edit.toPlainText())

    def copy_transcript(self):
        text = self.transcript_text_edit.toPlainText()
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def save_timestamps(self):
        self.save_text(self.transcript_with_timestamps_text_edit.toPlainText())

    def copy_timestamps(self):
        text = self.transcript_with_timestamps_text_edit.toPlainText()
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def get_data(self):
        data = self.extract_data('import_data')
        self.emit_data.emit(data)

    def save_file(self, content, title, file_filter, mode='w', encoding='utf-8', default_extension=None):
        # Check if content is valid
        if isinstance(content, BytesIO):
            if content.getbuffer().nbytes == 0:  # Check if the buffer is empty
                QMessageBox.critical(self, "Error", "No content available to save.")
                return
            content = content.getvalue()  # Convert BytesIO to bytes
        elif not content:  # Check for None or empty content
            QMessageBox.critical(self, "Error", "No content available to save.")
            return

        # Set default directory to the home directory
        # default_directory = str(QDir.homePath())  # Home Directory
        default_directory = str(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))  # Home Documents Directory

        # filename, _ = QFileDialog.getSaveFileName(None, title, "", file_filter)
        filename, _ = QFileDialog.getSaveFileName(None, title, default_directory, file_filter)
        if filename:
            path = Path(filename)
            if default_extension and not path.suffix:
                path = path.with_suffix(default_extension)
            try:
                with open(path, mode, encoding=None if 'b' in mode else encoding) as file:
                    file.write(content)
                # print(f"File saved to {path}")
            except Exception as e:
                print(f"Error saving file: {e}")
                QMessageBox.critical(self, "Save Error", f"Failed to save the file: {e}")
        else:
            # print("Save cancelled.")
            pass

    def save_text(self, content, encoding='utf-8'):
        self.save_file(content, "Save Text File", "Text Files (*.txt);;All Files (*)", 'w',
                       encoding, default_extension=".txt")


    def extract_data(self, process):
        if self.imported:
            data = TranscriptWindowData(process=process,
                                        mp3_file=self.transcriber.get_memory_file(),
                                        file_name=self.mp3_input.text(),
                                        transcript=self.transcript_text_edit.toPlainText(),
                                        time_stamps=self.transcript_with_timestamps_text_edit.toPlainText(),
                                        file_info=self.info_text_edit.toPlainText(),
                                        )

            self.emit_data.emit(data)
            if process == 'import_data':
                self.reset_fields(extract=True)
            return data
        else:
            dialog = WSMessageDialog("Info", 'No data to import')
            dialog.confirm()
            return None


# Main application execution
if __name__ == "__main__":
    app = QApplication(sys.argv)
    main_window = CapTranscriptWindow()
    main_window.show()
    sys.exit(app.exec())

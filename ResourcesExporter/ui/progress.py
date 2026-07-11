# -*- coding: utf-8 -*-
"""Progress dialog for RenderDoc exporter."""

from PySide2.QtCore import QTimer
from PySide2 import QtWidgets, QtCore, QtGui


class MProgressDialog(QtWidgets.QProgressDialog):
    def __init__(
        self,
        status=u"progress...",
        button_text=u"Cancel",
        minimum=0,
        maximum=100,
        parent=None,
        title="",
    ):
        super(MProgressDialog, self).__init__(parent)
        # Remove default system window chrome (title bar + drop shadow) to
        # avoid the layered-shadow artefact that appears when multiple
        # QProgressDialog instances are created in quick succession.
        self.setWindowFlags(
            self.windowFlags() |
            QtCore.Qt.NoDropShadowWindowHint
        )
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.setWindowTitle(title if title else u"正在导出资源")
        self.setMinimumWidth(520)
        # Style the built-in progress bar directly — avoids double-bar stacking
        # that occurs when setBar() inserts a second QProgressBar on top of the
        # one QProgressDialog already owns.
        self.setStyleSheet(
            """
            QProgressBar {
                color: white;
                border: 1px solid #2a5a2a;
                border-radius: 6px;
                background: #2b2b2b;
                text-align: center;
                font-weight: bold;
                min-height: 22px;
                max-height: 22px;
            }

            QProgressBar::chunk {
                background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0,
                stop: 0    #1a7a1a,
                stop: 0.4  #2db52d,
                stop: 0.5  #28b028,
                stop: 1    #1a7a1a );
                border-radius: 5px;
                border: none;
            }
            """
        )
        # Align percentage text in the built-in bar
        _bar = self.findChild(QtWidgets.QProgressBar)
        if _bar:
            _bar.setAlignment(QtCore.Qt.AlignCenter)
        self.setLabelText(status)
        self.setCancelButtonText(button_text)
        self.setRange(minimum, maximum)
        self.setValue(minimum)

        # NOTE show the progressbar without blocking
        self.show()
        QtWidgets.QApplication.processEvents()

    @classmethod
    def loop(cls, seq, **kwargs):
        self = cls(**kwargs)
        if not kwargs.get("maximum"):
            self.setMaximum(len(seq))
        for i, item in enumerate(seq, 1):

            if self.wasCanceled():
                break
            try:
                yield i, item  # with body executes here
            except:
                import traceback

                traceback.print_exc()
                self.deleteLater()
            self.setValue(i)
        self.deleteLater()

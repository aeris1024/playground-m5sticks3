"""Software-rendered head with a consistent right-handed camera frame."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .orientation import Quaternion


class HeadView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.orientation = Quaternion()
        self.viewpoint = "rear"
        self.setMinimumSize(360, 280)

    def camera(self):
        # right x up points from the object towards the camera. The same normal
        # must determine visibility/depth; reversing it mirrors solid geometry.
        if self.viewpoint == "front":
            return (0., 1., 0.), (-.52999894, 0., .847998304), (.847998304, 0., .52999894)
        return (0., -1., 0.), (.52999894, 0., .847998304), (-.847998304, 0., .52999894)

    def project(self, vector) -> QPointF:
        right, up, _ = self.camera()
        scale = min(self.width(), self.height())*.24
        return QPointF(self.width()*.5+scale*sum(a*b for a, b in zip(right, vector)),
                       self.height()*.48-scale*sum(a*b for a, b in zip(up, vector)))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#15212e"))
        origin = self.project((0, 0, 0))
        for axis, label, color in (((1.8, 0, 0), "+X 前", "#ff7070"),
                                   ((0, 1.8, 0), "+Y 左", "#70d690"),
                                   ((0, 0, 1.8), "+Z 上", "#70baff")):
            tip = self.project(axis)
            painter.setPen(QPen(QColor(color), 2))
            painter.drawLine(origin, tip)
            painter.drawText(tip+QPointF(3, -3), label)
        vertices = [(x, y, z) for x, y, z in (
            (-.65, -.55, -.75), (.65, -.55, -.75), (.65, .55, -.75), (-.65, .55, -.75),
            (-.65, -.55, .75), (.65, -.55, .75), (.65, .55, .75), (-.65, .55, .75),
            (1.12, 0, .05), (.65, -.2, -.12), (.65, .2, -.12), (.65, 0, .3))]
        rotated = [self.orientation.rotate(v) for v in vertices]
        faces = [((0, 1, 2, 3), "#52687a"), ((4, 5, 6, 7), "#a6c9df"),
                 ((0, 4, 5, 1), "#668da7"), ((3, 2, 6, 7), "#719db6"),
                 ((0, 3, 7, 4), "#54758d"), ((1, 5, 6, 2), "#e1bf9a"),
                 ((8, 9, 10), "#d99c69"), ((8, 11, 9), "#efc09b"),
                 ((8, 10, 11), "#f3d3ae")]
        _, _, normal = self.camera()
        # Draw far faces first, using the same camera as the projection.
        def depth(face):
            return sum(sum(a*b for a, b in zip(normal, rotated[i])) for i in face[0])/len(face[0])
        for indices, color in sorted(faces, key=depth):
            painter.setBrush(QColor(color))
            painter.setPen(QPen(QColor("#15212e"), 1))
            painter.drawPolygon(QPolygonF([self.project(rotated[i]) for i in indices]))
            if indices == (1, 5, 6, 2):
                painter.setBrush(QColor("#25384a"))
                painter.setPen(Qt.PenStyle.NoPen)
                for y in (-.25, .25):
                    eye = self.project(self.orientation.rotate((.66, y, .28)))
                    painter.drawEllipse(eye, 3.5, 3.5)
        # Overlay a labelled head-forward direction, visible from behind too.
        start = self.project(self.orientation.rotate((0, 0, .9)))
        tip = self.project(self.orientation.rotate((1.35, 0, .9)))
        painter.setPen(QPen(QColor("#ffd26b"), 3))
        painter.drawLine(start, tip)
        painter.setBrush(QColor("#ffd26b"))
        painter.drawEllipse(tip, 3.5, 3.5)
        painter.drawText(tip+QPointF(5, -5), "顔の向き")
        painter.setPen(QColor("#d9e6ef"))
        painter.drawText(12, 23, "頭の姿勢（後方から）" if self.viewpoint == "rear" else "頭の姿勢（前方から・対面）")
        painter.end()

"""下载引擎接口：内嵌 libtorrent 与外部 qBittorrent 的公共契约。

业务层（限额守门、订阅调度、API）只依赖本接口，引擎可替换。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class TaskState(str, Enum):
    DOWNLOADING = "downloading"
    PAUSED = "paused"  # 等待队列（限额排队或人工暂停）
    CHECKING = "checking"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TorrentState:
    """引擎上报的单任务状态快照（限额记账与 UI 渲染共用）。"""

    sha: str  # v1 infohash（hex），任务唯一键
    name: str
    state: TaskState
    size: int = 0  # 需要下载的总字节
    done: int = 0  # 已完成字节
    downloaded: int = 0  # 累计实际下载字节（限额记账用，单调递增）
    rate_down: int = 0  # 下载速度 B/s
    eta: int | None = None  # 预计剩余秒数；None=不可估
    sequential: bool = False  # 顺序下载（边下边看）
    save_path: str = ""
    added_at: float = 0.0  # 入队时间戳（等待队列排序）
    order: int = 0  # 入队序号（同时间戳时的稳定排序）
    error: str | None = None


class EngineError(RuntimeError):
    pass


class Engine(ABC):
    """下载引擎接口。所有方法除 list() 外都应快速返回；耗时操作由引擎内部线程化。"""

    @abstractmethod
    def start(self):
        """初始化/连接引擎。"""

    @abstractmethod
    def stop(self):
        """优雅关闭。"""

    @abstractmethod
    def add(self, data: bytes, *, paused: bool, save_path: str, sequential: bool = False, category: str = "") -> str:
        """添加 .torrent 原始字节，返回任务 sha（v1 infohash）。重复添加应幂等返回已有 sha。"""

    @abstractmethod
    def pause(self, sha: str):
        """暂停任务（进入等待队列语义）。"""

    @abstractmethod
    def resume(self, sha: str):
        """恢复任务。"""

    @abstractmethod
    def remove(self, sha: str, with_files: bool = False):
        """移除任务；with_files 时连已下载文件一并删除。"""

    @abstractmethod
    def list(self) -> list[TorrentState]:
        """当前全部任务的状态快照。"""

    @abstractmethod
    def set_global_limit(self, down_bps: int | None):
        """全局下载限速（B/s）；None 或 0 表示不限速。"""

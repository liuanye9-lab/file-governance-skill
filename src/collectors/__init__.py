from .base import BaseCollector
from .wechat import WechatCollector
from .inbox import InboxCollector
from .local_folder import LocalFolderCollector


def get_collectors(config: dict, db) -> list[BaseCollector]:
    collectors = []
    sources = config.get("sources", {})
    if sources.get("wechat", {}).get("enabled"):
        collectors.append(WechatCollector(sources["wechat"], db))
    if sources.get("inbox", {}).get("enabled"):
        collectors.append(InboxCollector(sources["inbox"], db))
    for folder in sources.get("local_folders", []) or []:
        if isinstance(folder, str):
            collectors.append(LocalFolderCollector({"path": folder}, db))
        elif isinstance(folder, dict) and folder.get("path"):
            collectors.append(LocalFolderCollector(folder, db))
    return collectors

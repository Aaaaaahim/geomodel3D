"""错误码与统一异常体系（8.7）。

错误码结构：``模块-错误类别-序号``，例如 ``MDL-IPL-002``（建模-插值病态）。
所有业务异常继承 :class:`GeoModelError`，携带错误码、上下文与恢复建议，
经 ``ExceptionHandler`` 转为错误事件，禁止裸抛到 UI。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class ErrorCode(str, Enum):
    """系统错误码（节选，按模块分组，可持续扩充）。"""

    # 工程 / 通用
    GEN_CFG_001 = "GEN-CFG-001"  # 配置校验失败
    GEN_IO_001 = "GEN-IO-001"  # 文件读写失败
    GEN_STATE_001 = "GEN-STATE-001"  # 非法状态流转
    PRJ_OPN_001 = "PRJ-OPN-001"  # 工程包损坏
    PRJ_OPN_002 = "PRJ-OPN-002"  # 工程版本不兼容
    PRJ_SAV_001 = "PRJ-SAV-001"  # 工程保存失败

    # 数据导入 M1
    DAT_PAR_001 = "DAT-PAR-001"  # 必填列缺失
    DAT_VAL_001 = "DAT-VAL-001"  # 结构校验失败
    DAT_CRS_001 = "DAT-CRS-001"  # 坐标系判定失败

    # 建模 M2
    MDL_IPL_002 = "MDL-IPL-002"  # 插值病态
    MDL_CUT_001 = "MDL-CUT-001"  # 断层切割失败
    MDL_RES_001 = "MDL-RES-001"  # 资源超限
    MDL_ENG_001 = "MDL-ENG-001"  # 引擎不可用
    MDL_BND_001 = "MDL-BND-001"  # 建模范围与数据不匹配

    # 储量 M4
    RSV_IND_001 = "RSV-IND-001"  # 指标校验失败
    RSV_GEO_001 = "RSV-GEO-001"  # 实体未封闭

    # 剖面 M5
    SEC_CRS_001 = "SEC-CRS-001"  # 缺少 CRS，禁止导出（BR-06）

    # 导出 M8
    EXP_CHK_001 = "EXP-CHK-001"  # 导出前检查单未通过


# 错误码 -> 默认恢复建议
SUGGESTIONS: Dict[ErrorCode, str] = {
    ErrorCode.GEN_CFG_001: "检查配置项取值是否在允许范围内，或点击“恢复默认”。",
    ErrorCode.GEN_IO_001: "确认文件未被占用、路径存在且具备读写权限。",
    ErrorCode.GEN_STATE_001: "请先完成当前任务或取消后再执行该操作。",
    ErrorCode.PRJ_OPN_001: "尝试从最近版本快照恢复，或检查工程包是否完整。",
    ErrorCode.PRJ_OPN_002: "使用当前版本打开只读，或升级工程 schema 后重试。",
    ErrorCode.PRJ_SAV_001: "检查磁盘剩余空间与工程目录写权限后重试。",
    ErrorCode.DAT_PAR_001: "在导入向导的列映射页补全缺失列，或使用标准模板。",
    ErrorCode.DAT_VAL_001: "查看数据质量报告，修复或剔除问题记录后重试。",
    ErrorCode.DAT_CRS_001: "手动指定源/目标 CRS 后重新导入。",
    ErrorCode.MDL_IPL_002: "已回退到局部 IDW 并记录置信度；建议增加钻孔或降低分辨率。",
    ErrorCode.MDL_CUT_001: "已回滚到切割前一致状态；请调整断层参数后重试。",
    ErrorCode.MDL_RES_001: "已自动降采样；可缩小建模范围或提高网格分辨率。",
    ErrorCode.MDL_ENG_001: "引擎未安装或被隔离；请安装对应可选依赖或切换引擎策略。",
    ErrorCode.MDL_BND_001: "请重设工程包围盒使其覆盖钻孔孔口与分层深度（可在导入时自动计算）。",
    ErrorCode.RSV_IND_001: "检查工业指标取值（品位/厚度/体重）是否合理。",
    ErrorCode.RSV_GEO_001: "先执行模型体检修复，确保实体封闭后再计算储量。",
    ErrorCode.SEC_CRS_001: "在工程设置中指定 CRS 后再导出剖面成果。",
    ErrorCode.EXP_CHK_001: "按检查单提示修复后再导出，或选择“强制导出”并说明原因。",
}


class GeoModelError(Exception):
    """业务异常基类。

    Attributes:
        code: 错误码。
        message: 面向用户的简明原因。
        context: 便于排障的结构化上下文。
        suggestion: 恢复建议（缺省取 :data:`SUGGESTIONS`）。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        suggestion: Optional[str] = None,
    ) -> None:
        super().__init__(f"[{code.value}] {message}")
        self.code = code
        self.message = message
        self.context = context or {}
        self.suggestion = suggestion or SUGGESTIONS.get(code, "请查看日志获取更多信息。")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "suggestion": self.suggestion,
            "context": self.context,
        }


class ConfigurationError(GeoModelError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("code", ErrorCode.GEN_CFG_001)
        super().__init__(message=message, **kwargs)


class ProjectFormatError(GeoModelError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("code", ErrorCode.PRJ_OPN_001)
        super().__init__(message=message, **kwargs)


class EngineUnavailableError(GeoModelError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("code", ErrorCode.MDL_ENG_001)
        super().__init__(message=message, **kwargs)


__all__ = [
    "ErrorCode",
    "SUGGESTIONS",
    "GeoModelError",
    "ConfigurationError",
    "ProjectFormatError",
    "EngineUnavailableError",
]

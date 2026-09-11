"""战法规则与参数路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from ..models import RuleSetUpdate, build_criteria
from ..store import rule_store
from ..strategy import clear_scan_cache
from ..utils import api_ok

logger = logging.getLogger(__name__)

router = APIRouter(tags=["战法规则"])


@router.get("/rules", summary="战法规则全文与参数")
async def get_rules():
    """返回 RuleSet 全文 + 规则出处说明（limitUp / entry / discipline / avoid）。"""
    rule_set = rule_store().get()
    return api_ok({"ruleSet": rule_set.model_dump(), "criteria": build_criteria(rule_set)})


@router.put("/rules", summary="更新策略参数")
async def update_rules(payload: RuleSetUpdate):
    """局部更新策略参数并持久化，返回更新后的完整 RuleSet。"""
    patch = payload.model_dump(exclude_unset=True, exclude_none=True)
    rule_set = rule_store().update(patch)
    clear_scan_cache()  # 参数变化后重新扫描
    logger.info("策略参数已更新：%s", patch)
    return api_ok(rule_set.model_dump())

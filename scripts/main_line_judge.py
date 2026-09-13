# -*- coding: utf-8 -*-
# 主线两档判定模块: 候选(early)=catalyst强, 确认(confirmed)=候选+动量/资金转强
CATALYST_TH = 0.7   # 候选阈值(产业催化分>=0.7)

def stage(catalyst_score, base_momentum_score, rs60_ex=None, breakout=None, fund_acc_5=None):
    """返回 {stage: 'none'/'候选'/'确认', candidate: bool, confirmed: bool}
    base_momentum_score: 动量+资金综合分(>0 表示转强). 也接受单个确认信号。
    """
    candidate = (catalyst_score is not None and catalyst_score >= CATALYST_TH)
    # 确认: 候选 + 至少一个动量/资金转强信号
    confirm_signal = (
        (base_momentum_score is not None and base_momentum_score > 0)
        or (rs60_ex is not None and rs60_ex > 0)
        or (breakout is not None and bool(breakout))
        or (fund_acc_5 is not None and fund_acc_5 > 0)
    )
    confirmed = candidate and confirm_signal
    stage_name = '确认' if confirmed else ('候选' if candidate else '无')
    return {'stage': stage_name, 'candidate': bool(candidate), 'confirmed': bool(confirmed),
            'catalyst_score': catalyst_score, 'base_momentum_score': base_momentum_score}

def rank_score(catalyst_score, base_momentum_score):
    """catalyst 主导排序分(候选优先), 动量/资金做 tiebreak."""
    c = catalyst_score if catalyst_score is not None else 0.0
    b = base_momentum_score if base_momentum_score is not None else 0.0
    return c*1.0 + 0.3*b

def decision(stage_name):
    """给交易agent的动作建议."""
    if stage_name == '确认':
        return '建仓(产业逻辑+动量资金确认)'
    if stage_name == '候选':
        return '观察(产业逻辑强但动量资金未确认; 等待二浪突破/资金进来再介入)'
    return '不做(无产业催化或未确认)'

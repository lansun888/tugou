
    async def _check_drawdown_stop_loss(self, pos: Position) -> bool:
        """策略五：回撤止损"""
        cfg = self.config.get("trailing_stop", {})
        pullback_limit = cfg.get("pullback_threshold", 40)
        
        if pos.highest_price > 0 and pos.highest_price > pos.buy_price_bnb * 1.1: # 至少涨一点
            pullback = (pos.highest_price - pos.current_price) / pos.highest_price * 100
            if pullback >= pullback_limit:
                 # Check N confirmation logic
                 token = pos.token_address
                 reason = "drawdown_40"
                 
                 if token not in self.pending_stop_loss:
                    self.pending_stop_loss[token] = {
                        "count": 1, 
                        "first_trigger_time": time.time(),
                        "reason": reason
                    }
                    logger.info(f"{pos.token_name} 触发回撤止损 ({reason})，等待二次确认...")
                    return False
                 else:
                    pending = self.pending_stop_loss[token]
                    elapsed = time.time() - pending["first_trigger_time"]
                    
                    if elapsed > 15:
                        logger.info(f"{pos.token_name} 二次确认回撤止损 ({reason})，执行卖出!")
                        del self.pending_stop_loss[token]
                        await self._execute_sell(pos, 100, reason)
                        return True
                    else:
                        return False
        return False

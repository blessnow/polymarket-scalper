# Polymarket BTC 5MIN Scalper

高频套利 bot，利用 Polymarket CLOB 与 Binance 现货价格延迟进行套利。

## 核心原理

```
Binance 现货价格变化 → Polymarket CLOB 滞后 0.3%+ → 在重新定价前下单 → 吃差价
```

## 快速开始

### 1. 安装依赖

```bash
cd polymarket-scalper
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env 填入你的 API keys（可选，默认 dry run）
```

### 3. 运行（模拟模式）

```bash
./run.sh
```

或手动运行：

```bash
python src/main.py
```

### 4. 查看 Dashboard

打开浏览器访问：**http://localhost:5000**

默认 `DRY_RUN=true`，不会真实下单，只模拟交易。

## Dashboard 功能

✅ **实时监控** - Binance vs Polymarket 价格差  
✅ **机会追踪** - 所有检测到的套利机会  
✅ **模拟交易** - Dry run 模式下的虚拟交易  
✅ **PnL 曲线** - 48小时盈亏历史  
✅ **统计面板** - 胜率、交易次数、平均价差等  

## 配置说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TRADING_ENABLED` | false | 是否启用真实交易 |
| `DRY_RUN` | true | 模拟模式（不实际下单） |
| `RISK_PER_TRADE` | 0.005 | 单笔风险 0.5% |
| `DAILY_LOSS_LIMIT` | 0.02 | 每日亏损上限 2% |
| `HARD_STOP_LOSS` | -0.004 | 硬止损 -0.4% |
| `MIN_SPREAD_THRESHOLD` | 0.003 | 最小价差阈值 0.3% |
| `MAX_POSITION_SIZE` | 100 | 最大持仓数量 |

## 架构

```
src/
├── main.py              # 主程序
├── binance_ws.py        # Binance WebSocket 实时价格
├── polymarket_clob.py   # Polymarket CLOB API
├── delay_detector.py    # 延迟检测 + 套利逻辑
└── risk_manager.py      # 风控模块
```

## 核心模块

### 1. Binance WebSocket (`binance_ws.py`)
- 实时 BTC 价格流
- 5分钟 K线数据
- 价格变化计算

### 2. Polymarket CLOB (`polymarket_clob.py`)
- 获取 BTC 5MIN UP/DOWN 市场
- 订单簿查询
- 下单/撤单

### 3. 延迟检测 (`delay_detector.py`)
- 计算 Binance vs Polymarket 价差
- 信号收敛检测（SMA、RSI、动量）
- 套利机会识别

### 4. 风控 (`risk_manager.py`)
- 仓位大小计算
- 每日亏损限制
- 硬止损
- 交易统计

## 风险警告

⚠️ **高风险策略**

1. **市场风险** - Polymarket 可能禁用 bot 或加延迟保护
2. **流动性风险** - 5MIN 市场流动性有限
3. **技术风险** - 网络延迟、API 故障
4. **资金风险** - 可能快速亏损

## 获取 API Keys

### Polymarket
1. 访问 https://polymarket.com
2. 连接钱包
3. 在 Settings 获取 API Key

### Binance
- WebSocket 无需 API Key
- REST API 需要在 Binance 注册并创建 API Key

## 免责声明

本项目仅供学习和研究目的。使用本代码进行交易的所有风险由使用者自行承担。

## License

MIT

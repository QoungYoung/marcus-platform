# -*- coding: utf-8 -*-
"""chain_map 生成器 v3 (2026-09-08): 环节龙头 = 规则层(成分池->fina主营核实->mv排序) + AI裁决层
流程: 每环节 成分池(mv龙头定位) -> fina主营核实剔伪 -> 核实集 total_mv 排序取 top3; rejected 边界股送 DeepSeek
语义复核(依据 fina 主营文本+销售额), in_segment 且 conf>=0.85 回补 leading(source=ai); borderline(0.5<=conf<0.85)/
unknown 保留审计并置 needs_verify; AI 的 suggested_kw 累积到 data/chain_kw_suggestions.json 供每周词典校准自举。
v3 相对 v2: rejected 不再静默丢弃——每个被规则拒绝的候选都有 AI 复核或审计记录; 纯规则版可用 --no-ai 保持 v2 行为。
用法: python3 chain_map.py [YYYYMMDD] [--no-ai]
输出: data/chain_map_{date}.json + data/chain_map_{date}_v2_cmp.json + data/chain_kw_suggestions.json(追加)
"""
import sys, os, json, sqlite3, time, requests, urllib3
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
urllib3.disable_warnings()
DATA = os.environ.get('DATA_DIR', '/app/data')

SEED = {
 "AI/算力/科技":  [
  {
   "role": "upstream",
   "label": "上游·芯片/光器件/材料设备",
   "concepts": [
    "AI芯片",
    "存储芯片",
    "光通信模块",
    "CPO概念",
    "PCB",
    "液冷概念"
   ],
   "kw": [
    "芯片",
    "半导体",
    "光模块",
    "光器件",
    "PCB",
    "液冷",
    "制冷",
    "散热",
    "存储",
    "处理器",
    "CPU",
    "面板",
    "显示",
    "晶圆",
    "设备",
    "光通信模块",
    "AI芯片",
    "半导体设备",
    "电子工艺装备",
    "光收发模块", "收发模块", "集成电路",
    "云端芯片",
    "光通信器件",
    "光互联",
    "智能计算芯片"
   ]
  },
  {
   "role": "mid",
   "label": "中游·算力基建/云",
   "concepts": [
    "算力概念",
    "数据中心",
    "云计算",
    "边缘计算",
    "东数西算"
   ],
   "kw": [
    "算力",
    "服务器",
    "数据中心",
    "IDC",
    "云计算",
    "算网",
    "光模块",
    "数据中心光互联",
    "光通信",
    "数据中心互联",
    "光通信收发模块",
    "光互联"
   ]
  },
  {
   "role": "downstream",
   "label": "下游·模型/应用/终端",
   "concepts": [
    "AI应用",
    "AIGC概念",
    "多模态AI",
    "AI智能体",
    "AI语料",
    "DeepSeek概念",
    "ChatGPT概念",
    "Kimi概念",
    "智谱AI",
    "AI眼镜",
    "人工智能"
   ],
   "kw": [
    "AI",
    "人工智能",
    "大模型",
    "应用",
    "软件",
    "语料",
    "智能体",
    "眼镜",
    "数字人",
    "信息服务",
    "互联网"
   ]
  }
 ],
 "传媒/游戏":  [
  {
   "role": "upstream",
   "label": "内容/研发制作",
   "concepts": [
    "网络游戏",
    "影视概念",
    "短剧互动游戏"
   ],
   "kw": [
    "游戏",
    "影视",
    "剧",
    "内容",
    "研发",
    "IP",
    "网络游戏",
    "游戏研发",
    "游戏运营"
   ]
  },
  {
   "role": "downstream",
   "label": "平台/分发/服务",
   "concepts": [
    "在线教育",
    "职业教育",
    "体育产业"
   ],
   "kw": [
    "平台",
    "教育",
    "培训",
    "体育",
    "赛事",
    "发行"
   ]
  }
 ],
 "消费/内需":  [
  {
   "role": "upstream",
   "label": "品牌/制造",
   "concepts": [
    "白酒",
    "新消费",
    "消费电子概念"
   ],
   "kw": [
    "白酒",
    "酒",
    "消费",
    "电子",
    "品牌",
    "制造",
    "消费电子制造",
    "ODM",
    "移动终端",
    "消费电子",
    "精密功能件",
    "AI终端",
    "消费电子ODM",
    "智能硬件制造"
   ]
  },
  {
   "role": "downstream",
   "label": "渠道/场景",
   "concepts": [
    "免税概念",
    "新零售",
    "零售概念",
    "旅游概念",
    "文娱消费"
   ],
   "kw": [
    "免税",
    "零售",
    "商超",
    "旅游",
    "景区",
    "餐饮",
    "文娱"
   ]
  }
 ],
 "农业":  [
  {
   "role": "upstream",
   "label": "上游·种业/粮食种植/收储加工",
   "concepts": [
    "种子",
    "转基因",
    "粮食种植",
    "粮食概念"
   ],
   "kw": [
    "种子",
    "种业",
    "种苗",
    "育种",
    "转基因",
    "粮食",
    "谷物",
    "水稻",
    "玉米",
    "小麦",
    "大豆",
    "种植",
    "农垦",
    "农场",
    "耕地",
    "土地",
    "粮油",
    "大米",
    "面粉",
    "食用油",
    "种",
    "粮"
   ]
  },
  {
   "role": "mid",
   "label": "中游·养殖/饲料/乳/水产",
   "concepts": [
    "生猪养殖",
    "肉鸡养殖",
    "水产养殖",
    "渔业",
    "饲料",
    "乳业"
   ],
   "kw": [
    "生猪",
    "养殖",
    "畜牧",
    "种猪",
    "肉鸡",
    "鸡",
    "禽",
    "水产",
    "渔业",
    "饲料",
    "乳",
    "奶",
    "牧场",
    "屠宰"
   ]
  },
  {
   "role": "downstream",
   "label": "下游·农资/农化",
   "concepts": [
    "农药兽药",
    "生态农业"
   ],
   "kw": [
    "农药",
    "兽药",
    "化肥",
    "复合肥",
    "农化",
    "除草剂",
    "杀虫",
    "农资",
    "农机",
    "农业机械",
    "生态农业",
    "生物肥",
    "有机",
    "钾肥",
    "钾盐",
    "磷复肥"
   ]
  }
 ],
 "军工/航天":  [
  {
   "role": "upstream",
   "label": "上游·军工材料/锻件/复材(航空装备池内细分)",
   "concepts": [
    "航空装备Ⅱ"
   ],
   "kw": [
    "材料",
    "超材料",
    "非金属",
    "有色金属",
    "钛",
    "合金",
    "锻造",
    "锻件",
    "隐身",
    "涂层",
    "碳纤维",
    "复材",
    "石英",
    "特种",
    "铸造",
    "预浸料",
    "纤维"
   ]
  },
  {
   "role": "mid",
   "label": "中游·分系统/军工电子/元器件(含航天电源)",
   "concepts": [
    "军工电子Ⅱ",
    "航天装备Ⅱ"
   ],
   "kw": [
    "连接器",
    "电子元器件",
    "元器件",
    "红外",
    "光电",
    "集成电路",
    "芯片",
    "模拟",
    "雷达",
    "通信",
    "电能源",
    "无人系统",
    "射频",
    "组件",
    "模块",
    "感知",
    "探测",
    "电源",
    "防务",
    "装备",
    "电子",
    "能源",
    "机载"
   ]
  },
  {
   "role": "downstream",
   "label": "下游·整机总装/宇航产品",
   "concepts": [
    "航空装备Ⅱ",
    "航天装备Ⅱ"
   ],
   "kw": [
    "航空产品",
    "航空制造业",
    "航空制造",
    "飞机制造",
    "无人机",
    "航空航天产品",
    "宇航制造",
    "宇航",
    "航天产品",
    "火箭",
    "发动机",
    "飞机",
    "整机",
    "总装"
   ]
  }
 ],
 "电力/公用":  [
  {
   "role": "upstream",
   "label": "发电设备(综合电力设备商)",
   "concepts": [
    "综合电力设备商"
   ],
   "kw": [
    "能源装备",
    "发电设备",
    "电气设备",
    "锅炉",
    "汽轮机",
    "核电"
   ]
  },
  {
   "role": "downstream",
   "label": "核电运营",
   "concepts": [
    "核力发电"
   ],
   "kw": [
    "核电",
    "电力",
    "发电"
   ]
  },
  {
   "role": "downstream",
   "label": "水电运营",
   "concepts": [
    "水力发电"
   ],
   "kw": [
    "水电",
    "电力"
   ]
  },
  {
   "role": "downstream",
   "label": "火电运营",
   "concepts": [
    "火力发电"
   ],
   "kw": [
    "电力",
    "发电",
    "火电",
    "热力",
    "售电"
   ]
  },
  {
   "role": "downstream",
   "label": "绿电运营(风电)",
   "concepts": [
    "风力发电"
   ],
   "kw": [
    "电力",
    "发电",
    "风电",
    "风力"
   ]
  },
  {
   "role": "downstream",
   "label": "绿电运营(光伏)",
   "concepts": [
    "光伏发电"
   ],
   "kw": [
    "发电",
    "太阳能",
    "光伏",
    "电力"
   ]
  },
  {
   "role": "mid",
   "label": "电网自动化/二次设备",
   "concepts": [
    "电网自动化设备"
   ],
   "kw": [
    "电网",
    "配电",
    "变电",
    "输配电",
    "继电保护",
    "调度",
    "电厂",
    "自动化",
    "智能电表",
    "电力电子",
    "电气装备",
    "电气机械"
   ]
  },
  {
   "role": "mid",
   "label": "输变电设备/特高压",
   "concepts": [
    "特高压"
   ],
   "kw": [
    "输变电",
    "特高压",
    "变压器",
    "开关",
    "绝缘子",
    "电抗",
    "换流",
    "电气设备",
    "输配电"
   ]
  }
 ],
 "机器人/智能制造":  [
  {
   "role": "upstream",
   "label": "机器人执行器/精密传动",
   "concepts": [
    "机器人执行器"
   ],
   "kw": [
    "减速器",
    "谐波",
    "执行器",
    "电机",
    "丝杠",
    "传动",
    "齿轮",
    "伺服"
   ]
  },
  {
   "role": "upstream",
   "label": "伺服/工控(运动控制)",
   "concepts": [
    "工控设备"
   ],
   "kw": [
    "伺服",
    "变频器",
    "驱动",
    "运动控制",
    "工业自动化",
    "智能制造",
    "工控"
   ]
  },
  {
   "role": "mid",
   "label": "机器人本体/智能装备集成",
   "concepts": [
    "机器人概念"
   ],
   "kw": [
    "工业机器人",
    "机器人产品",
    "机器人本体",
    "智能装备",
    "自动化装配",
    "机器人与智能产线"
   ]
  },
  {
   "role": "mid",
   "label": "数控机床(工业母机/机床)",
   "concepts": [
    "机床工具"
   ],
   "kw": [
    "数控",
    "机床",
    "主轴"
   ]
  },
  {
   "role": "downstream",
   "label": "机器视觉检测",
   "concepts": [
    "其他自动化设备"
   ],
   "kw": [
    "机器视觉",
    "视觉",
    "检测设备"
   ]
  },
  {
   "role": "downstream",
   "label": "激光/智能制造装备",
   "concepts": [
    "激光设备"
   ],
   "kw": [
    "激光",
    "智能制造装备"
   ]
  }
 ],
 "汽车/智驾":  [
  {
   "role": "mid",
   "label": "汽车整车",
   "concepts": [
    "汽车整车"
   ],
   "kw": [
    "汽车",
    "整车",
    "乘用车",
    "新能源汽车",
    "客车",
    "商用车",
    "重卡"
   ]
  },
  {
   "role": "upstream",
   "label": "智驾/汽车电子(域控座舱/毫米波)",
   "concepts": [
    "汽车电子电气系统",
    "毫米波概念"
   ],
   "kw": [
    "汽车电子",
    "智能座舱",
    "毫米波",
    "雷达",
    "域控",
    "座舱",
    "智能驾驶",
    "自动驾驶",
    "HUD",
    "零部件业务"
   ]
  },
  {
   "role": "upstream",
   "label": "智驾感知/激光雷达/车路协同",
   "concepts": [
    "激光雷达"
   ],
   "kw": [
    "激光雷达",
    "智能交通",
    "车载",
    "短程通信",
    "路侧",
    "车路",
    "V2X"
   ]
  },
  {
   "role": "downstream",
   "label": "一体化压铸/车身轻量化",
   "concepts": [
    "汽车一体化压铸"
   ],
   "kw": [
    "压铸",
    "铸件",
    "铝合金",
    "一体化压铸",
    "汽车零部件",
    "轻量化"
   ]
  },
  {
   "role": "downstream",
   "label": "底盘/线控/动力系统零部件",
   "concepts": [
    "底盘与发动机系统"
   ],
   "kw": [
    "汽车零部件",
    "制动",
    "底盘",
    "转向",
    "悬架",
    "电控",
    "线控",
    "减振",
    "汽车类",
    "汽车行业"
   ]
  },
  {
   "role": "downstream",
   "label": "汽车零部件(玻璃/车灯/内外饰/安全)",
   "concepts": [
    "汽车零部件"
   ],
   "kw": [
    "汽车零部件",
    "零部件业务",
    "车灯",
    "汽车玻璃",
    "内外饰",
    "内饰",
    "汽车电子",
    "智能座舱",
    "汽车安全",
    "汽车行业",
    "汽车类"
   ]
  },
  {
   "role": "downstream",
   "label": "汽车热管理",
   "concepts": [
    "汽车热管理"
   ],
   "kw": [
    "热交换器",
    "热管理",
    "换热",
    "空调",
    "液冷",
    "温控"
   ]
  }
 ],
 "稳增长/基建":  [
  {
   "role": "upstream",
   "label": "上游·建材(水泥/玻纤/防水/装修)",
   "concepts": [
    "水泥制造",
    "玻璃玻纤",
    "防水材料",
    "涂料",
    "装修建材"
   ],
   "kw": [
    "水泥",
    "建材",
    "混凝土",
    "骨料",
    "玻纤",
    "玻璃",
    "纤维",
    "防水",
    "涂料",
    "墙面漆",
    "漆",
    "石膏",
    "板材",
    "装饰材料",
    "装修",
    "岩棉",
    "陶瓷",
    "瓷砖",
    "砂浆",
    "材料",
    "管材"
   ]
  },
  {
   "role": "mid",
   "label": "中游·建筑央企基建施工",
   "concepts": [
    "工程建设",
    "基建市政工程"
   ],
   "kw": [
    "工程",
    "建筑",
    "建设",
    "基础设施",
    "施工",
    "承包",
    "房建",
    "市政",
    "公路",
    "铁路",
    "隧道",
    "桥梁",
    "安装",
    "勘测",
    "设计",
    "工程承包"
   ]
  },
  {
   "role": "downstream",
   "label": "下游·国际工程(一带一路出海)",
   "concepts": [
    "国际工程"
   ],
   "kw": [
    "国际工程",
    "工程承包",
    "工程技术",
    "工程总承包",
    "工程",
    "承包",
    "海外",
    "境外工程",
    "国际"
   ]
  }
 ],
 "医药":  [
  {
   "role": "upstream",
   "label": "上游·CXO研发外包/原料药服务",
   "concepts": [
    "医疗研发外包",
    "CRO"
   ],
   "kw": [
    "医药研发",
    "研发服务",
    "研发外包",
    "CRO",
    "CDMO",
    "临床",
    "非临床",
    "安评",
    "化学业务",
    "医药行业",
    "医药主营业务",
    "科学研究",
    "研究",
    "原料药",
    "化学药",
    "生物分析",
    "试验服务",
    "药物",
    "定制",
    "中间体",
    "商业化"
   ]
  },
  {
   "role": "mid",
   "label": "中游·创新药研发制造",
   "concepts": [
    "创新药",
    "化学制剂",
    "生物制品"
   ],
   "kw": [
    "医药制造",
    "医药行业",
    "医药工业",
    "制药",
    "药品",
    "制剂",
    "抗肿瘤",
    "生物药",
    "化学药",
    "原料药",
    "临床",
    "研发",
    "肿瘤",
    "注射液",
    "片剂",
    "创新药"
   ]
  },
  {
   "role": "mid",
   "label": "中游·中药制造",
   "concepts": [
    "中药Ⅱ"
   ],
   "kw": [
    "中药",
    "中成药",
    "医药行业",
    "医药工业",
    "医药制造",
    "医药商业",
    "制药",
    "药品",
    "阿胶",
    "颗粒",
    "饮片",
    "成药",
    "丸",
    "膏",
    "散",
    "片"
   ]
  },
  {
   "role": "downstream",
   "label": "下游·医疗器械(设备/耗材/IVD)",
   "concepts": [
    "医疗设备",
    "医疗耗材",
    "体外诊断"
   ],
   "kw": [
    "医疗器械",
    "医疗设备",
    "医疗耗材",
    "设备",
    "耗材",
    "体外诊断",
    "诊断",
    "试剂",
    "影像",
    "检测",
    "仪器",
    "介入",
    "血糖",
    "监护",
    "透析",
    "超声",
    "内镜",
    "材料",
    "防护",
    "家用",
    "医疗"
   ]
  }
 ],
 "新能源/电池":  [
  {
   "role": "upstream",
   "label": "上游·电池材料(正负极/电解液/隔膜/前驱体)",
   "concepts": [
    "电池化学品"
   ],
   "kw": [
    "锂离子电池",
    "锂电池",
    "电池材料",
    "电解液",
    "隔膜",
    "隔离膜",
    "正极",
    "负极",
    "三元",
    "磷酸铁锂",
    "前驱体",
    "材料",
    "碳酸锂",
    "氢氧化锂",
    "六氟磷酸锂",
    "锂",
    "镍",
    "钴",
    "导电",
    "添加剂",
    "电池化学品",
    "化学品",
    "化工"
   ]
  },
  {
   "role": "mid",
   "label": "中游·电芯/电池系统/精密结构件",
   "concepts": [
    "锂电池"
   ],
   "kw": [
    "电池",
    "锂离子",
    "电芯",
    "电池包",
    "动力电池",
    "储能",
    "消费类",
    "圆柱",
    "软包",
    "方形",
    "PACK",
    "电池系统",
    "盖帽",
    "结构件",
    "电子",
    "工业"
   ]
  },
  {
   "role": "downstream",
   "label": "下游·锂电专用设备",
   "concepts": [
    "锂电专用设备"
   ],
   "kw": [
    "锂电",
    "电池",
    "设备",
    "卷绕",
    "叠片",
    "涂布",
    "化成分容",
    "检测",
    "焊接",
    "装配",
    "注液",
    "制片",
    "极片",
    "干燥",
    "整线",
    "电芯"
   ]
  }
 ],
 "半导体/芯片":  [
  {
   "role": "upstream",
   "label": "上游·半导体材料/设备(含光刻)",
   "concepts": [
    "半导体材料",
    "半导体设备",
    "光刻胶",
    "光刻机"
   ],
   "kw": [
    "半导体",
    "晶圆",
    "硅片",
    "硅",
    "电子特气",
    "特气",
    "光刻胶",
    "光刻",
    "掩膜",
    "抛光",
    "清洗",
    "刻蚀",
    "薄膜",
    "沉积",
    "离子注入",
    "靶材",
    "前驱体",
    "设备",
    "检测",
    "量测",
    "测试机",
    "分选",
    "材料",
    "零部件",
    "陶瓷",
    "真空",
    "工艺"
   ]
  },
  {
   "role": "mid",
   "label": "中游·芯片设计",
   "concepts": [
    "数字芯片设计",
    "模拟芯片设计"
   ],
   "kw": [
    "芯片",
    "集成电路",
    "半导体",
    "设计",
    "处理器",
    "CPU",
    "GPU",
    "存储",
    "存储器",
    "控制器",
    "MCU",
    "模拟",
    "射频",
    "电源",
    "接口",
    "传感器",
    "图像",
    "音频",
    "视频",
    "无线",
    "基带",
    "SoC",
    "计算",
    "微控制器",
    "FPGA",
    "安全芯片",
    "信号",
    "驱动",
    "光电",
    "分立"
   ]
  },
  {
   "role": "mid2",
   "label": "中游·晶圆制造/代工",
   "concepts": [
    "集成电路制造"
   ],
   "kw": [
    "集成电路",
    "晶圆",
    "代工",
    "制造",
    "半导体",
    "工艺",
    "特色工艺",
    "功率",
    "分立器件",
    "碳化硅",
    "MEMS",
    "器件",
    "硅基"
   ]
  },
  {
   "role": "downstream",
   "label": "下游·封装测试",
   "concepts": [
    "集成电路封测"
   ],
   "kw": [
    "封装",
    "测试",
    "封测",
    "集成电路",
    "晶圆",
    "芯片",
    "成品",
    "模组",
    "基板",
    "减薄",
    "划片",
    "键合"
   ]
  }
 ],
 "资源/周期":  [
  {
   "role": "upstream",
   "label": "上游·贵金属黄金",
   "concepts": [
    "黄金",
    "贵金属"
   ],
   "kw": [
    "黄金",
    "金",
    "银",
    "贵金属",
    "金属",
    "矿",
    "金矿",
    "金锭"
   ]
  },
  {
   "role": "mid",
   "label": "中游·稀土锂资源冶炼",
   "concepts": [
    "稀土",
    "锂",
    "能源金属"
   ],
   "kw": [
    "稀土",
    "锂",
    "碳酸锂",
    "氢氧化锂",
    "矿",
    "盐",
    "氧化物",
    "冶炼"
   ]
  },
  {
   "role": "downstream",
   "label": "下游·有色加工冶炼",
   "concepts": [
    "工业金属",
    "铜",
    "铝",
    "铅锌",
    "钨",
    "钼"
   ],
   "kw": [
    "铜",
    "铝",
    "铅",
    "锌",
    "锡",
    "钨",
    "钼",
    "钴",
    "镍",
    "电解",
    "冶炼",
    "有色",
    "金属",
    "矿",
    "合金",
    "压延",
    "加工"
   ]
  }
 ],
 "金融":  [
  {
   "role": "upstream",
   "label": "上游·金融IT/金融软件",
   "concepts": [
    "数字货币",
    "软件开发",
    "垂直应用软件"
   ],
   "kw": [
    "互联网金融",
    "金融信息服务",
    "金融行业",
    "金融领域",
    "金融科技",
    "软件业收入",
    "银行IT",
    "银行金融",
    "第三方支付",
    "支付业务",
    "数字人民币",
    "金融软件",
    "金融云"
   ]
  },
  {
   "role": "mid",
   "label": "中游·证券券商",
   "concepts": [
    "证券Ⅱ"
   ],
   "kw": [
    "证券",
    "券商",
    "经纪",
    "财富管理",
    "投行",
    "投资银行",
    "手续费",
    "佣金",
    "自营",
    "融资融券",
    "做市",
    "资产管理"
   ]
  },
  {
   "role": "downstream",
   "label": "下游·银行保险",
   "concepts": [
    "银行",
    "保险"
   ],
   "kw": [
    "利息",
    "贷款",
    "存款",
    "银行",
    "保险",
    "寿险",
    "财险",
    "保费",
    "承保",
    "零售金融",
    "对公金融",
    "票据",
    "托管"
   ]
  }
 ],
 "银行":  [
  {
   "role": "seg1",
   "label": "银行·商行/国有大行",
   "concepts": [
    "银行"
   ],
   "kw": [
    "金融业务",
    "公司金融",
    "个人金融",
    "零售金融",
    "贷款",
    "信贷",
    "利息",
    "存款",
    "中间业务",
    "财富管理"
   ]
  }
 ],
}
LEAD_TOP_N = 3
VERIFY_POOL_MV_N = 20  # 2026-09-08: 12->20(金健米业案例: union mv第13被截断漏票, 中小市值细分票需更宽池)
VERIFY_POOL_CONCEPT_N = 6
AI_CONF_MIN = 0.85
AI_BORDERLINE_MIN = 0.5
AI_MAX_PER_SEG = 10
AI_WORKERS = 4   # 2026-09-09: AI 裁决并发(15主题全量曾>900s超时)

def concept_stocks(db, cname, limit):
    try:
        cur = db.cursor()
        cur.execute("SELECT ts_code FROM stock_concept_map WHERE concept_name=? LIMIT ?", (cname, limit))
        return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print('concept err', cname, e); return []

def fetch_mv(pro, date8):
    """全市场 daily_basic(total_mv 万元); 当日盘后未入库时逐日回退"""
    d = date8
    for _ in range(6):
        try:
            df = pro.daily_basic(trade_date=d, fields='ts_code,total_mv')
            if df is not None and not df.empty and len(df) > 1000:
                return {str(r['ts_code']): float(r['total_mv']) for _, r in df.iterrows()}, d
        except Exception as e:
            print('mv', d, 'err', str(e)[:80], flush=True)
        d = str(int(d) - 1)
    return {}, None

FINA_CACHE = {}
AI_CACHE = {}
FINA_TTL = 60 * 86400
AI_TTL = 7 * 86400
_FORCE_FINA = False
_FINA_MISS = 0

def _cache_files():
    return os.path.join(DATA, 'chain_fina_cache.json'), os.path.join(DATA, 'chain_ai_cache.json')

def load_caches():
    global FINA_CACHE, AI_CACHE
    try:
        p, q = _cache_files()
        FINA_CACHE = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
        AI_CACHE = json.load(open(q, encoding='utf-8')) if os.path.exists(q) else {}
    except Exception:
        FINA_CACHE = {}; AI_CACHE = {}
    print('caches | fina', len(FINA_CACHE), 'ai', len(AI_CACHE), flush=True)

def save_caches():
    try:
        p, q = _cache_files()
        json.dump(FINA_CACHE, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
        json.dump(AI_CACHE, open(q, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception as e:
        print('cache save err', str(e)[:80], flush=True)

def fina_fetch_raw(pro, ts, force=False):
    """fina 主营原文 top10 缓存(60天TTL); 命中免网络, miss 才拉取"""
    global _FINA_MISS
    if not force and not _FORCE_FINA:
        e = FINA_CACHE.get(ts)
        if e and (time.time() - e.get('t', 0)) < FINA_TTL:
            return e['items']
    items = []
    try:
        df = pro.fina_mainbz(ts_code=ts)
        if df is not None and not df.empty:
            df = df.sort_values('bz_sales', ascending=False)
            items = [{'bz': str(r.get('bz_item') or '')[:40], 'sales': float(r.get('bz_sales') or 0)}
                     for _, r in df[~df['bz_item'].isin(['行业', '产品', '地区'])].head(10).iterrows()]
    except Exception:
        pass
    FINA_CACHE[ts] = {'t': time.time(), 'items': items}
    _FINA_MISS += 1
    time.sleep(0.05)
    return items

def fina_verdict(pro, ts, seg, names):
    v = {'ts': ts, 'name': names.get(ts, ''), 'mainbz': [], 'hit': [], 'ok': False}
    items = fina_fetch_raw(pro, ts)
    if items:
        tops = [x['bz'] for x in items[:6]]
        v['mainbz'] = tops
        hits = [t for t in tops if any(k.lower() in t.lower() for k in seg['kw'])]
        v['hit'] = hits[:3]
        v['ok'] = len(hits) > 0
    return v

def load_fina_bz(pro, ts):
    return fina_fetch_raw(pro, ts)[:8]

SYSTEM = (
'你是 A股产业链成分研究员。判断公司主营是否真正属于给定产业链环节(该环节是整条产业链的细分场景)。'
'口径: 1) fina主营 bz_item 文本(带销售额)是首要依据, 先看主营是什么、是否主导; '
'2) 概念成分归属只是线索不可当依据(概念表收录脏, 常有跨界巨无霸); '
'3) 只回答“是否属于该环节”, 公司属于同一条产业链的其它环节判 out; '
'4) 主营名目与环节无字面重叠不等于 out(词表可能有盲区), 要按语义判断; '
'5) 信息不足或主营过杂无法确定判 unknown, 不要硬猜。'
'输出严格 JSON(仅对象, 不要代码围栏): {"verdict": "in_segment"|"out"|"unknown", "confidence": 0~1, '
'"reason": "一句话依据(<=40字)", "suggested_kw": [若 in_segment 给出 1-3 个能代表主营的环节词, 否则空数组]}')

_AI_ENABLED = None
def ai_enabled():
    global _AI_ENABLED
    if _AI_ENABLED is None:
        try:
            sys.path.insert(0, '/app/core')
            from core.api_client import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL
            _AI_ENABLED = bool(DEEPSEEK_API_KEY and DEEPSEEK_API_HOST)
        except Exception:
            _AI_ENABLED = False
    return _AI_ENABLED

def llm_call(system_prompt, user_prompt):
    sys.path.insert(0, '/app/core')
    from core.api_client import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL
    url = "https://" + DEEPSEEK_API_HOST + "/v1/chat/completions"
    body = {"model": DEEPSEEK_MODEL, "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}],
        "temperature": 0.1, "max_tokens": 900}
    r = requests.post(url, headers={"Authorization": "Bearer " + DEEPSEEK_API_KEY,
                                    "Content-Type": "application/json"},
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=90)
    content = r.json()["choices"][0]["message"]["content"]
    return parse_json(content)

def parse_json(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content[3:]
        if content.startswith("json"): content = content[4:]
    if content.endswith("```"): content = content[:-3]
    i, j = content.find("{"), content.rfind("}")
    if i < 0 or j < i: raise ValueError("no json: " + content[:120])
    return json.loads(content[i:j+1])

def llm_retry(system_prompt, user_prompt):
    last = ''
    for _ in range(2):
        try:
            return llm_call(system_prompt, user_prompt)
        except Exception as e:
            last = str(e)
            time.sleep(1.2)
    return {"verdict": "unknown", "confidence": 0.0, "reason": "llm_err:" + last[:50], "suggested_kw": []}

def ai_review_one(pro, ts, name, seg, theme):
    bz = load_fina_bz(pro, ts)
    user = json.dumps({"theme": theme, "segment": seg["label"], "role": seg.get("role"),
                       "环节概念": seg.get("concepts", []),
                       "company": {"ts": ts, "name": name, "fina主营top(销售额万元)": bz},
                       "规则初筛": "rejected: 主营文本未命中环节关键词"}, ensure_ascii=False)
    return llm_retry(SYSTEM, user)

def kw_suggest_append(theme, seg, ts, name, verdict):
    """累积 AI suggested_kw 到 data/chain_kw_suggestions.json (同票同段同词去重)"""
    p = os.path.join(DATA, 'chain_kw_suggestions.json')
    try:
        acc = json.load(open(p, encoding='utf-8'))
    except Exception:
        acc = {'entries': []}
    known = set()
    for e in acc['entries']:
        for w in e.get('kw', []):
            known.add((e['theme'], e['seg'], e['ts'], w))
    new = [w for w in (verdict.get('suggested_kw') or []) if (theme, seg['label'], ts, w) not in known]
    if new:
        acc['entries'].append({'theme': theme, 'seg': seg['label'], 'ts': ts, 'name': name,
                               'kw': new, 'confidence': verdict.get('confidence'),
                               'reason': (verdict.get('reason') or '')[:60], 'date': time.strftime('%Y%m%d')})
        json.dump(acc, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return len(new)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    no_ai = '--no-ai' in sys.argv
    global _FORCE_FINA
    full = '--full' in sys.argv
    _FORCE_FINA = '--refresh-fina' in sys.argv
    date8 = args[0] if args else None
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    names = {}
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
        if sb is not None and not sb.empty: names = dict(zip(sb['ts_code'], sb['name']))
    except Exception as e:
        print('stock_basic fail', str(e)[:80])
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    t0 = time.time()
    import datetime
    today = datetime.date.today().strftime('%Y%m%d')
    date8 = date8 or today
    try:
        from app.api.market import _get_tushare_pro as _p
        cal = _p().trade_cal(exchange='SSE', start_date='20260801', end_date=date8, is_open='1')
        if cal is not None and not cal.empty:
            date8 = str(cal['cal_date'].astype(str).max()).replace('-', '')
    except Exception as e:
        print('trade_cal fail, use date=', date8, str(e)[:80], flush=True)
    mv, mv_date = fetch_mv(pro, date8)
    load_caches()
    use_ai = (not no_ai) and ai_enabled()
    print('chain_map v3 | date', date8, '| mv_date', mv_date, '| ai', 'ON' if use_ai else ('off(--no-ai)' if no_ai else 'UNAVAILABLE'), flush=True)
    out = {'date': date8, 'mv_date': mv_date, 'generated_by': 'chain_map_v3_mv_verified_ai',
           'method': 'rules(mv+fina核实) top3 -> rejected 边界股 AI 语义复核回补; conf>=%.2f 回补, 词典自举' % AI_CONF_MIN,
           'ai': {'enabled': use_ai, 'conf_min': AI_CONF_MIN}, 'themes': []}
    cmp_rows = []
    seen_promoted = set()   # 跨段去重标记用
    for theme, segs in SEED.items():
        t_seg = []
        for seg in segs:
            codes, big, seen_c, seen_b = [], [], set(), set()
            for c in seg['concepts']:
                for ts in concept_stocks(db, c, 12):
                    if ts not in seen_c: seen_c.add(ts); codes.append(ts)
                for ts in concept_stocks(db, c, 100):
                    if ts not in seen_b: seen_b.add(ts); big.append(ts)
            mv_sorted = sorted(big, key=lambda c: -mv.get(c, 0)) if mv else []
            pool = list(dict.fromkeys([c for c in mv_sorted[:VERIFY_POOL_MV_N] if mv.get(c)] + codes[:VERIFY_POOL_CONCEPT_N]))
            verdicts = {}
            for ts in pool:
                verdicts[ts] = fina_verdict(pro, ts, seg, names)
            verified = [verdicts[ts] for ts in pool if verdicts[ts]['ok']]
            verified.sort(key=lambda v: -mv.get(v['ts'], 0))
            for i, v in enumerate(verified):
                v['mv'] = round(mv.get(v['ts'], 0), 0); v['mv_rank'] = i + 1
            rejected = sorted([verdicts[ts] for ts in pool if not verdicts[ts]['ok']],
                              key=lambda v: -mv.get(v['ts'], 0))
            for v in rejected:
                v['mv'] = round(mv.get(v['ts'], 0), 0)
                v['reason'] = 'mainbz_not_matched_by_kw'
            # ---- AI 裁决层 ----
            ai_stats = {'reviewed': 0, 'promoted': 0, 'out': 0, 'unknown': 0, 'borderline': 0, 'err': 0}
            kw_new = 0
            promoted_ai = []
            borderline = []
            todo = [rv for rv in rejected[:AI_MAX_PER_SEG]] if use_ai else []
            a_map = {}
            if use_ai and todo:
                miss = []
                for rv in todo:
                    key = theme + '|' + seg['label'] + '|' + rv['ts']
                    ae = AI_CACHE.get(key)
                    if (not full) and ae and (time.time() - ae.get('t', 0)) < AI_TTL:
                        a_map[rv['ts']] = ae.get('a') or {}
                    else:
                        miss.append((rv, key))
                if miss:
                    import concurrent.futures as _cf
                    with _cf.ThreadPoolExecutor(max_workers=AI_WORKERS) as _ex:
                        _res = list(_ex.map(
                            lambda w: (w[0], w[1], ai_review_one(pro, w[0]['ts'], w[0].get('name', ''), seg, theme)), miss))
                    for rv, key, a in _res:
                        a_map[rv['ts']] = a
                        AI_CACHE[key] = {'t': time.time(), 'a': a}
            for rv in todo:
                a = a_map.get(rv['ts'], {})
                ai_stats['reviewed'] += 1
                v = a.get('verdict', 'unknown'); conf = float(a.get('confidence') or 0)
                rv['ai'] = {'verdict': v, 'confidence': round(conf, 2), 'reason': (a.get('reason') or '')[:60]}
                if v == 'in_segment' and conf >= AI_CONF_MIN:
                    ai_stats['promoted'] += 1
                    if rv['ts'] in seen_promoted: rv['cross_seg'] = True
                    seen_promoted.add(rv['ts'])
                    promoted_ai.append(rv)
                    kw_new += kw_suggest_append(theme, seg, rv['ts'], rv.get('name', ''), a)
                elif v == 'in_segment' and conf >= AI_BORDERLINE_MIN:
                    ai_stats['borderline'] += 1; borderline.append(rv)
                elif v == 'out':
                    ai_stats['out'] += 1
                elif v == 'unknown':
                    ai_stats['unknown'] += 1
                else:
                    ai_stats['err'] += 1
            # final leading: rule verified + ai promoted 统一按 mv 排 top3
            final_pool = []
            for v in verified:
                v2 = dict(v); v2['source'] = 'rule_mv'; final_pool.append(v2)
            for rv in promoted_ai:
                rv2 = dict(rv); rv2['source'] = 'ai_review'; rv2['ok'] = True; final_pool.append(rv2)
            final_pool.sort(key=lambda x: -x.get('mv', 0))
            leading = final_pool[:LEAD_TOP_N]
            ai_kept = [v for v in rejected if v['ts'] not in [x['ts'] for x in promoted_ai + borderline]]
            needs_verify = (len(leading) < LEAD_TOP_N) or bool(ai_stats['unknown']) or bool(borderline)
            seg_node = {'role': seg['role'], 'label': seg['label'], 'concepts': seg['concepts'],
                        'pool_n': len(big), 'mv_date': mv_date, 'candidates': codes[:6],
                        'leading_verified': leading,
                        'verified_all': [dict(v, source='rule_mv') for v in verified],
                        'rejected': ai_kept, 'needs_verify': needs_verify,
                        'ai_reviewed': ai_stats['reviewed'], 'ai_promoted': promoted_ai, 'ai_borderline': borderline,
                        'ai_stats': ai_stats, 'kw_suggest_new': kw_new, 'method': 'mv_verified+ai' if use_ai else 'mv_verified'}
            t_seg.append(seg_node)
            old_names = [verdicts[ts]['name'] for ts in codes[:2] if ts in verdicts]
            new_names = [x['name'] + ('*' if x.get('source') == 'ai_review' else '') for x in leading]
            ai_prom_names = [x['name'] for x in promoted_ai]
            cmp_rows.append({'theme': theme, 'seg': seg['label'], 'old_top2': old_names, 'new_leading': new_names,
                             'ai_promoted': ai_prom_names, 'ai_stats': ai_stats, 'needs_verify': needs_verify,
                             'pool_n': len(big)})
            print('[' + theme + '][' + seg['label'] + '] pool=' + str(len(big)) + ' verified=' + str(len(verified))
                  + ' NEW ' + str(new_names) + ' | ai_reviewed=' + str(ai_stats['reviewed']) + ' promoted=' + str(ai_prom_names)
                  + ' out=' + str(ai_stats['out']) + ' unknown=' + str(ai_stats['unknown']) + ' borderline=' + str(ai_stats['borderline'])
                  + ' kw_new=' + str(kw_new) + ' needs_verify=' + str(needs_verify), flush=True)
        out['themes'].append({'theme': theme, 'segments': t_seg,
                              'completeness': {'segments': len(t_seg), 'has_leader': any(len(s['leading_verified']) for s in t_seg)}})
        print('[' + theme + '] 环节' + str(len(t_seg)) + ' 用时' + str(int(time.time() - t0)) + 's', flush=True)
    p = os.path.join(DATA, 'chain_map_' + date8 + '.json')
    with open(p, 'w', encoding='utf-8') as f: json.dump(out, f, ensure_ascii=False, indent=1)
    cp = os.path.join(DATA, 'chain_map_' + date8 + '_v2_cmp.json')
    with open(cp, 'w', encoding='utf-8') as f:
        json.dump({'date': date8, 'mv_date': mv_date, 'ai': out['ai'], 'rows': cmp_rows}, f, ensure_ascii=False, indent=1)
    save_caches()
    print('cached | fina_miss', _FINA_MISS, flush=True)
    print('WROTE', p); print('WROTE', cp)

if __name__ == '__main__':
    main()
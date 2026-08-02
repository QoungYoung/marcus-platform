## 1. PostgreSQL 閺佺増宓佸Ο鈥崇€?
- [x] 1.1 閸?`backend/app/models/market_orm.py` 閺傛澘顤?7 娑?SQLAlchemy 濡€崇€烽敍姝歋tockPool`閵嗕梗Sector`閵嗕梗ConceptSector`閵嗕梗StockConceptMap`閵嗕梗StockSectorMap`閵嗕梗EtfPool`閵嗕梗SectorConfig`閿涘牅瀵岄柨?閸烆垯绔寸痪锔芥将娑?SQLite 濠ф劒绔撮懛杈剧礉JSON 閸掓鏁?`JSONB`閿?- [x] 1.2 閹?SQLite 閻滅増婀佺槐銏犵穿鐞涖儵缍?PG 缁便垹绱╅敍姝歴tock_pool(industry/market/is_st)`閵嗕梗stock_sector_map(sector_name)`閵嗕梗concept_sectors(concept_name)`閵嗕梗stock_concept_map(concept_name)`
- [x] 1.3 閸?`backend/app/database.py` 閻?`init_db()` 娑?import 濞夈劌鍞介弬鐗埬侀崹瀣剁礉娴?`create_all` 閻㈢喐鏅?
## 2. 閺佺増宓佹潻浣盒╅懘姘拱

- [x] 2.1 閺傛澘缂?`scripts/migrate_market_data_to_pgsql.py`閿涙俺顕伴崣?`data/stock_pool.db` 閻?5 瀵姾銆冮妴涔ata/cache.db` 閻?`etf_pool`閵嗕梗data/trades.db` 閻?`sector_config`閿涘本妲х亸鍕晸閸?PG 鐎电懓绨茬悰?- [x] 2.2 閺€顖涘瘮 `--dry-run`閿涙矮绮庨幍鎾冲祪濠ф劘銆冪悰灞炬殶閿涘牆鎯?`watchlist` 鐞涘本鏆熼敍澶夌瑢閻╊喗鐖ｇ悰銊︽Ё鐏忓嫸绱濇稉宥呭晸閸?- [x] 2.3 濮濓絽绱℃潻鎰攽閿涙碍绔荤粚铏规窗閺嶅洩銆?閳?閸忋劑鍣?`INSERT` 閳?鏉堟挸鍤崥鍕€冮崘娆忓弳鐞涘本鏆?閳?鐎?`data/trades.db` 閹笛嗩攽 `DROP TABLE IF EXISTS watchlist`閿涘牆鍨归梽銈呭閹垫挸宓冪悰灞炬殶閿涘绱辨稉宥呭灩闂?SQLite 濠ф劖鏋冩禒璁圭礉缂佹挻娼崜宥嗙墡妤?PG 鐞涘本鏆熸稉搴㈢爱娑撯偓閼?
## 3. 閸忓彉闊╅崣顏囶嚢娴犳挸鍋?
- [x] 3.1 閺傛澘缂?`backend/app/services/market_reference.py`閿涙瓪get_stock_name()`閵嗕梗get_stock_industry()`閵嗕梗search_stocks()`閵嗕梗get_concept_stocks()`閵嗕梗get_etf_pool()`閵嗕梗get_sector_config()`閿涘牆鐔€娴?`SessionLocal`閿?- [x] 3.2 娑撹櫣鍎圭捄顖氱窞閿涘牆鎮曠粔?鐞涘奔绗熼弻銉嚄閿涘顤冮崝鐘电叚 TTL 閸愬懎鐡ㄧ紓鎾崇摠閿涘矂浼╅崗宥夌彯妫版垼绻欑粩?PG 瀵扳偓鏉?
## 4. 閸氬海顏拠缁樻煙閸掑洦宕?PostgreSQL

- [x] 4.1 `backend/app/api/market.py`閿涙俺鍋傜粊銊︽偝缁鳖潿鈧笒TF 閹兼粎鍌ㄩ妴浣诡洤韫?閺夊灝娼￠弻銉嚄閺€纭呰泲 `market_reference`閿涘牏些闂?`sqlite3.connect`閿?- [x] 4.2 `backend/app/api/trades.py`閵嗕梗portfolio.py`閵嗕梗news.py`閵嗕梗indicator.py`閿涙俺鍋傜粊銊ユ倳/鐞涘奔绗熼弻銉嚄閺€纭呰泲 PG
- [x] 4.3 `backend/app/api/etf.py`閿涙氨鈥樼拋?ETF 閸掓銆冮幒銉ュ經缂?`XueqiuEngine.get_etf_pool_from_db()` 鐠囪鍩?PG 閺佺増宓?- [x] 4.4 `backend/app/services/industry_leaderboard.py`閵嗕梗local_data_provider.py`閿涙碍婢橀崸?閹存劕鍨庨懖鈩冪叀鐠囥垺鏁肩挧?PG

## 5. core/jobs 鐠囪鍟撻弬鐟板瀼閹?
- [x] 5.1 `core/stock_pool_manager.py`閿涙艾缂撶悰?閸愭瑥鍙嗛弨閫涜礋 `psycopg2` + `DATABASE_URL`閿涘畭INSERT ... ON CONFLICT ... DO UPDATE`閿涘苯銇囬幍褰掑櫤閻?`executemany` 閸掑棙澹?commit
- [x] 5.2 `core/xueqiu_engine.py`閿涙瓪_save_etf_pool_item()` 娑?`get_etf_pool_from_db()` 閺€閫涜礋鐠囪鍟?PG `etf_pool` 鐞?- [x] 5.3 jobs/瀹搞儱鍙跨拠缁樻煙閸掑洦宕查敍姝歫obs/stock_selector.py`閵嗕梗jobs/fund_flow.py`閵嗕梗jobs/market_scan.py`閵嗕梗apps/trader/etf_selector.py` 閻?stock_pool 閺屻儴顕楅弨纭呰泲 PG閿涘牆褰叉径宥囨暏娴犳挸鍋嶉幋?psycopg2閿?- [x] 5.4 `jobs/pre_market_scan.py` 閻?`SectorConfigManager`閿涙瓪_load_or_seed()`/`update_stocks()` 閺€閫涜礋鐠囪鍟?PG `sector_config`閿涘牏鈹栫悰?seed閵嗕礁鍞寸€涙绱︾€涙ü绻氶悾娆嶁偓涔ync_from_etf()` 娴犲懎鍨忛幑銏ｆ儰鎼存捇鈧岸浜鹃敍?- [x] 5.5 `scripts/seed_golden_pit_etf_config.py` 娑?`backend/scripts/seed_golden_pit_etf_config.py` 閻?`sector_config` 閸氬本顒?SQL 閺€閫涜礋 PG 鐠囶厽纭?
## 6. 闁氨鏁ら弻銉嚄閹恒儱褰?db.py 閺€褰掆偓?
- [x] 6.1 `open_db()` 閺€閫涜礋閸欏苯鎮楃粩顖濈熅閻㈡唻绱癭stock_pool`/`etf_pool` 閳?PostgreSQL閿涘畭news`/`trades`/`cache` 閳?SQLite 閸欘亣顕?- [x] 6.2 `GET /db/schema/{db}` 鐎?PG 閺佺増宓侀梿鍡氱箲閸?PG 鐞涖劎绮ㄩ弸鍕瑢閸掓琚崹?- [x] 6.3 `POST /db/write` 鐎?PG-backed 閺佺増宓侀梿鍡氱箲閸?HTTP 400閿涘奔绮庨崗浣筋啅闁鏆€ SQLite 閺佺増宓侀梿鍡楀晸閸?- [x] 6.4 娣囨繃瀵旈崫宥呯安缂佹挻鐎?`{rows, columns}` 娑撳秴褰夐敍灞剧叀鐠囥垹寮弫鏉垮闂冨弶鏁為崗銉礄`where`/`order_by` 閻ц棄鎮曢崡鏇熷灗閸楃姳缍呯粭锔肩礆

## 7. 妤犲矁鐦?
- [x] 7.1 閹笛嗩攽 `python scripts/migrate_market_data_to_pgsql.py --dry-run`閿涘本鐗崇€电懓鎮囩悰銊攽閺侀绗屽┃鎰閼?- [x] 7.2 濮濓絽绱￠幍褑顢戞潻浣盒╅敍灞剧墡妤?PG 閸氬嫯銆冪悰灞炬殶娑?SQLite 濠ф劒绔撮懛杈剧礉绾喛顓?`watchlist` 瀹歌弓绮?`data/trades.db` 閸掔娀娅?- [x] 7.3 閸氼垰濮?backend閿涘矂鐛欑拠?`/api/v1/market/search`閵嗕梗/api/v1/etf`閵嗕浇顢戞稉姘緲閸ф甯撮崣锝冣偓涔?db/query?db=stock_pool` 濮濓絽鐖?- [x] 7.4 妤犲矁鐦?`/db/write` 鐎?`stock_pool` 鏉╂柨娲?400閵嗕礁顕?`news` 閸愭瑥鍙嗗锝呯埗
- [ ] 7.5 鏉╂劘顢戞稉鈧▎?`jobs/stock_pool_manager.py` 閸掗攱鏌婇敍宀€鈥樼拋銈呭晸 PG 濮濓絽鐖舵稉鏃€妫?`sqlite3.connect` 濞堝鏆€
- [x] 7.6 鏉╂劘顢戞稉鈧▎锛勬磸閸撳秵澹傞幓蹇ョ礉绾喛顓?`SectorConfigManager` 娴?PG 閸旂姾娴?閺囧瓨鏌?`sector_config` 濮濓絽鐖
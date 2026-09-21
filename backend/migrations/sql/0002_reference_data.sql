-- 0002_reference_data — defaults that the app reads at runtime. All editable from the admin panel.

INSERT INTO plans (code, name_fa, name_en, period_days, limits, features, prices, position) VALUES
 ('free', 'رایگان', 'Free', NULL,
  '{"channels": 3, "playlists": 5, "daily_plays": 60, "download": false}',
  ARRAY['discover_weekly'], '{}', 0),
 ('pro_monthly', 'پرو ماهانه', 'Pro monthly', 30,
  '{"channels": -1, "playlists": -1, "daily_plays": -1, "download": true}',
  ARRAY['discover_weekly','all_mixes','ai_search','ai_playlist','share_playlist','collab_playlist','instant_notify','offline'],
  '{"IRR": 1490000, "XTR": 150}', 1),
 ('pro_yearly', 'پرو سالانه', 'Pro yearly', 365,
  '{"channels": -1, "playlists": -1, "daily_plays": -1, "download": true}',
  ARRAY['discover_weekly','all_mixes','ai_search','ai_playlist','share_playlist','collab_playlist','instant_notify','offline'],
  '{"IRR": 14900000, "XTR": 1500}', 2);

INSERT INTO channel_categories (slug, name_fa, name_en, position) VALUES
 ('pop-fa',        'پاپ فارسی',   'Persian pop',   1),
 ('rap-fa',        'رپ فارسی',    'Persian rap',   2),
 ('traditional',   'سنتی',        'Traditional',   3),
 ('instrumental',  'بی‌کلام',     'Instrumental',  4),
 ('foreign',       'خارجی',       'International', 5),
 ('remix',         'ریمیکس',      'Remix',         6),
 ('old-fa',        'قدیمی',       'Classic Persian', 7),
 ('local',         'محلی',        'Regional',      8);

INSERT INTO settings (key, value) VALUES
 ('trial_days', '7'),
 ('grace_days', '3'),
 ('referral_reward_days', '3'),
 ('payment_review_ttl_hours', '24'),
 ('tos_version', '"2026-09-01"');

INSERT INTO feature_flags (key, value, description) VALUES
 ('maintenance_mode', 'false', 'API returns 503 for non-admin requests'),
 ('ai_search', 'false', 'Semantic search / descriptive playlists (ADR-0012)'),
 ('dedup_threshold', '0.88', 'pg_trgm similarity for fuzzy duplicate detection'),
 ('dedup_threshold_no_artist', '0.95', 'Same, when the artist is unknown'),
 ('index_batch_delay_ms', '1500', 'Pause between MTProto history batches');

-- config holds the NON-secret settings only; merchant keys come from the environment.
INSERT INTO payment_providers (code, is_enabled, currency, config, position) VALUES
 ('stars',     true,  'XTR', '{}', 0),
 ('zarinpal',  false, 'IRR', '{"sandbox": true, "merchant_env": "ZARINPAL_MERCHANT_ID"}', 1),
 ('idpay',     false, 'IRR', '{"sandbox": true, "merchant_env": "IDPAY_API_KEY"}', 2),
 ('nextpay',   false, 'IRR', '{"merchant_env": "NEXTPAY_API_KEY"}', 3),
 ('card2card', false, 'IRR', '{"card_number": "", "holder_name": "", "bank": ""}', 4);

-- Common Persian artists with their Latin spellings. normalized_name / aliases are
-- already in normalize_key() form (casefolded, Arabic letters mapped, no diacritics).
INSERT INTO artists (name, normalized_name, latin_name, aliases) VALUES
 ('معین', 'معین', 'Moein', ARRAY['moein','moin']),
 ('گوگوش', 'گوگوش', 'Googoosh', ARRAY['googoosh','gugush']),
 ('ابی', 'ابی', 'Ebi', ARRAY['ebi','ebrahim hamedi']),
 ('داریوش', 'داریوش', 'Dariush', ARRAY['dariush','dariush eghbali','داریوش اقبالی']),
 ('هایده', 'هایده', 'Hayedeh', ARRAY['hayedeh','haydeh']),
 ('مهستی', 'مهستی', 'Mahasti', ARRAY['mahasti']),
 ('شادمهر عقیلی', 'شادمهر عقیلی', 'Shadmehr Aghili', ARRAY['shadmehr aghili','shadmehr','شادمهر']),
 ('سیروان خسروی', 'سیروان خسروی', 'Sirvan Khosravi', ARRAY['sirvan khosravi','sirvan','سیروان']),
 ('محسن یگانه', 'محسن یگانه', 'Mohsen Yeganeh', ARRAY['mohsen yeganeh','mohsen yegane']),
 ('محسن چاوشی', 'محسن چاوشی', 'Mohsen Chavoshi', ARRAY['mohsen chavoshi','chavoshi','چاوشی']),
 ('رضا صادقی', 'رضا صادقی', 'Reza Sadeghi', ARRAY['reza sadeghi']),
 ('حامد همایون', 'حامد همایون', 'Hamed Homayoun', ARRAY['hamed homayoun','hamed homayon']),
 ('مازیار فلاحی', 'مازیار فلاحی', 'Maziar Fallahi', ARRAY['maziar fallahi']),
 ('ماکان بند', 'ماکان بند', 'Macan Band', ARRAY['macan band','macan']),
 ('بهنام بانی', 'بهنام بانی', 'Behnam Bani', ARRAY['behnam bani']),
 ('علیرضا طلیسچی', 'علیرضا طلیسچی', 'Alireza Talischi', ARRAY['alireza talischi']),
 ('محمدرضا شجریان', 'محمدرضا شجریان', 'Mohammadreza Shajarian', ARRAY['mohammadreza shajarian','mohammad reza shajarian','shajarian','شجریان']),
 ('همایون شجریان', 'همایون شجریان', 'Homayoun Shajarian', ARRAY['homayoun shajarian']),
 ('علیرضا افتخاری', 'علیرضا افتخاری', 'Alireza Eftekhari', ARRAY['alireza eftekhari']),
 ('سیاوش قمیشی', 'سیاوش قمیشی', 'Siavash Ghomayshi', ARRAY['siavash ghomayshi','siavash ghomeishi']),
 ('فرهاد مهراد', 'فرهاد مهراد', 'Farhad Mehrad', ARRAY['farhad mehrad','farhad']),
 ('ویگن', 'ویگن', 'Viguen', ARRAY['viguen','vigen']),
 ('ستار', 'ستار', 'Sattar', ARRAY['sattar']),
 ('اندی', 'اندی', 'Andy', ARRAY['andy','andy madadian']),
 ('شهره', 'شهره', 'Shohreh', ARRAY['shohreh','shohreh solati']),
 ('لیلا فروهر', 'لیلا فروهر', 'Leila Forouhar', ARRAY['leila forouhar']),
 ('کامران و هومن', 'کامران و هومن', 'Kamran & Hooman', ARRAY['kamran hooman']),
 ('آرش', 'ارش', 'Arash', ARRAY['arash']),
 ('بنیامین بهادری', 'بنیامین بهادری', 'Benyamin Bahadori', ARRAY['benyamin bahadori','benyamin','بنیامین']),
 ('امید', 'امید', 'Omid', ARRAY['omid']),
 ('یاس', 'یاس', 'Yas', ARRAY['yas']),
 ('هیچکس', 'هیچکس', 'Hichkas', ARRAY['hichkas']),
 ('تتلو', 'تتلو', 'Tataloo', ARRAY['tataloo','amir tataloo','امیر تتلو']),
 ('رضا بهرام', 'رضا بهرام', 'Reza Bahram', ARRAY['reza bahram']),
 ('مهدی احمدوند', 'مهدی احمدوند', 'Mehdi Ahmadvand', ARRAY['mehdi ahmadvand']),
 ('حمید هیراد', 'حمید هیراد', 'Hamid Hiraad', ARRAY['hamid hiraad','hamid hirad']),
 ('پویا بیاتی', 'پویا بیاتی', 'Pouya Bayati', ARRAY['pouya bayati']),
 ('سالار عقیلی', 'سالار عقیلی', 'Salar Aghili', ARRAY['salar aghili']),
 ('مرتضی پاشایی', 'مرتضی پاشایی', 'Morteza Pashaei', ARRAY['morteza pashaei','morteza pashaee','پاشایی']),
 ('میثم ابراهیمی', 'میثم ابراهیمی', 'Meysam Ebrahimi', ARRAY['meysam ebrahimi']);

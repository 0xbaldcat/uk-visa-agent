# 英国访客签证 PoC：材料清单

更新时间：2026-08-22  
适用范围：Standard Visitor，探亲访问 + 英国定居亲属场景  
规则版本：`visitor_family_visit` / `1.0.0`

这份文件是当前 PoC 实际执行的材料清单，来源是
`config/visitor_family_visit.yaml`。它描述 demo 会收什么，不代表所有真实申请人的
完整法律清单，也不对签证结果作判断。

## 一、所有申请人都收

| 材料 | 用途 | PoC 提取字段 | 真实邮件 demo 文件名示例 |
|---|---|---|---|
| 护照个人信息页及既往签证页 | 核验身份、护照有效期和既往出行记录 | `holder_name`、`passport_number`、`expiry_date`、`nationality`、`prior_compliant_travel` | `passport.json` |
| 个人银行流水 | 展示资金与行程成本；访客签没有法定最低余额 | `account_holder_name`、`period_start`、`period_end`、`closing_balance`、`currency` | `bank_statements.json` |
| 往返行程单或预订记录 | 核验出入境日期和乘客身份 | `outbound_date`、`return_date`、`passenger_name` | `travel_itinerary.json` |
| 住宿证明 | 说明在英住宿安排 | `address`、`host_name`、`stay_start`、`stay_end` | `accommodation_proof.json` |
| 常住国约束证据 | 展示房产、家属、赡养、学习等回国约束 | `tie_types` | `home_ties_evidence.json` |

## 二、按案情条件追加

| 触发条件 | 追加材料 | PoC 提取字段 | 真实邮件 demo 文件名示例 |
|---|---|---|---|
| 在英国有定居或英国籍亲属，并由其接待 | 亲属邀请信 | `sponsor_name`、`sponsor_address`、`relationship`、`stay_start`、`stay_end`、`funding_offered` | `sponsor_invitation_letter.json` |
| 在英国有定居或英国籍亲属，并由其接待 | 担保人的英国身份或居留证明 | `sponsor_name`、`status_type` | `sponsor_status_proof.json` |
| 行程由第三方出资 | 出资人的银行流水 | `account_holder_name`、`period_start`、`period_end`、`closing_balance`、`currency` | `sponsor_financial_evidence.json` |
| 申请人受雇 | 雇主证明及准假信息 | `employer_name`、`job_title`、`leave_start`、`leave_end`、`annual_salary` | `employment_letter.json` |
| 申请人自雇 | 工商注册、税务记录和企业流水 | `business_name`、`registration_id`、`tax_year`、`declared_income`、`business_statement_period_start`、`business_statement_period_end` | `self_employment_evidence.json` |

## 三、当前演示客户实际需要的 8 项

演示客户是自雇申请人，由自己承担行程费用，在英国有定居姐姐并住在姐姐家。因此
本次 demo 的实例化清单为：

1. 护照个人信息页及既往签证页
2. 个人银行流水
3. 往返行程单或预订记录
4. 住宿证明
5. 亲属邀请信
6. 担保人的英国身份或居留证明
7. 工商注册、税务记录和企业流水
8. 常住国约束证据

不需要第三方出资人流水，也不需要雇主证明。

## 四、真实邮件 demo 的附件约定

- 当前两天 PoC 只读取结构化 JSON 测试附件，不解析真实 PDF 或图片。
- 文件名必须以上表的 evidence id 开头，系统才能映射到正确材料，例如
  `passport.json`、`bank_statements-aug.json`。
- JSON 中只使用上表列出的字段；额外字段不会进入案情记录。
- 演示只使用虚构数据，不发送真实护照、流水或其他敏感个人资料。


# 英国访客签证 PoC：材料清单

更新时间：2026-08-27

产品范围：UK Standard Visitor

已验证参考路线：`visitor_family_visit` / `1.1.0`

这份文件是当前 PoC 实际执行的材料清单，来源是
`config/routes.yaml` 组合的 Standard Visitor core、`purposes/family_visit`
和 applicant profile rule pack。它描述已验证参考路线会收什么，不代表所有真实申请人的
完整法律清单，也不对签证结果作判断。tourism 和 business 目前只是未验证 scaffold。

## 一、所有申请人都收

| 材料 | 用途 | PoC 提取字段 | 真实邮件 demo 文件名示例 |
|---|---|---|---|
| 护照个人信息页及既往签证页 | 核验身份、护照有效期和既往出行记录 | `holder_name`、`passport_number`、`expiry_date`、`nationality`、`prior_compliant_travel` | `passport.pdf` |
| 个人银行流水 | 展示资金与行程成本；访客签没有法定最低余额 | `account_holder_name`、`period_start`、`period_end`、`closing_balance`、`currency` | `bank_statements.pdf` |
| 往返行程单或预订记录 | 核验出入境日期和乘客身份 | `outbound_date`、`return_date`、`passenger_name` | `travel_itinerary.pdf` |
| 住宿证明 | 说明在英住宿安排 | `address`、`host_name`、`stay_start`、`stay_end` | `accommodation_proof.docx` |
| 常住国约束证据 | 展示房产、家属、赡养、学习等回国约束 | `tie_types` | `home_ties_evidence.pdf` |

## 二、按案情条件追加

| 触发条件 | 追加材料 | PoC 提取字段 | 真实邮件 demo 文件名示例 |
|---|---|---|---|
| 在英国有定居或英国籍亲属，并由其接待 | 亲属邀请信 | `sponsor_name`、`sponsor_address`、`relationship`、`stay_start`、`stay_end`、`funding_offered` | `sponsor_invitation_letter.docx` |
| 在英国有定居或英国籍亲属，并由其接待 | 担保人的英国身份或居留证明 | `sponsor_name`、`status_type` | `sponsor_status_proof.png` |
| 行程由第三方出资 | 出资人的银行流水 | `account_holder_name`、`period_start`、`period_end`、`closing_balance`、`currency` | `sponsor_financial_evidence.pdf` |
| 申请人受雇 | 雇主证明及准假信息 | `employer_name`、`job_title`、`leave_start`、`leave_end`、`annual_salary` | `employment_letter.docx` |
| 申请人自雇 | 工商注册、税务记录和企业流水 | `business_name`、`registration_id`、`tax_year`、`declared_income`、`business_statement_period_start`、`business_statement_period_end` | `self_employment_evidence.pdf` |

## 三、needs-follow-up 演示客户实际需要的 8 项

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

- 当前 PoC 接受 PDF、DOCX、文本、图片和扫描 PDF；图片/扫描件在 live 模式使用
  Baidu OCR，offline fixture 使用同路径 `.ocr.txt` sidecar。
- 文件名应以上表的 evidence id 开头，系统才能自动映射到正确材料，例如
  `passport.pdf`、`bank_statements_aug.pdf`。少量常见别名也受支持。
- 本地抽取先运行；如配置 OpenAI-compatible document model，只会为仍缺失且
  在该材料 `extract` allow-list 中的字段提出 candidate。额外字段不会进入案情记录。
- `.json` 仍保留为开发者捷径，不是客户需要提交的材料格式。
- 演示只使用虚构数据，不发送真实护照、流水或其他敏感个人资料。

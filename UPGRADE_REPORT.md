# 升级报告

## 基本信息

| 项目 | 值 |
|------|-----|
| 仓库名 | flask-testing |
| 升级时间 | 2026-03-13 |
| 升级状态 | ✅ 成功 |

## Python 版本

| 升级前 | 升级后 |
|--------|--------|
| Python 2.6+ / 3.x | Python 3.13+ |

## 依赖变更

| 依赖 | 升级前 | 升级后 |
|------|--------|--------|
| Flask | 无版本限制 | 3.1.3 |
| blinker | 无版本限制 | 1.9.0 |
| twill | 0.9.1 (仅 Python 2) | 3.3.1 |
| wsgi-intercept | 未声明 | 1.13.1 (新增) |

## 代码修改

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| flask_testing/twill.py | API 适配 | StringIO 导入适配 Python 3 |
| flask_testing/twill.py | API 适配 | twill.get_browser() → TwillBrowser() |
| flask_testing/twill.py | API 适配 | 使用独立的 wsgi-intercept 包 |
| flask_testing/utils.py | Bug 修复 | _empty_render 参数顺序修正 (app, template, context) |
| flask_testing/utils.py | Bug 修复 | assertRedirects 支持相对路径 location |
| flask_testing/utils.py | Bug 修复 | assertTemplateUsed 错误信息格式修正 |
| tests/test_utils.py | Bug 修复 | BaseTestLiveServer 添加 create_app 实现 |
| setup.py | 依赖更新 | 移除 Python 2 兼容代码 |
| setup.py | 依赖更新 | 添加 twill 和 wsgi-intercept 到 tests_require |

## 测试结果

| 测试类型 | 结果 |
|----------|------|
| 通过 | ✅ 43 passed |
| 失败 | 0 failed |
| 警告 | 1 warning (unittest.TestResult 命名冲突) |

## 主要问题修复

### 1. twill 库 API 变化
- **问题**: twill 3.x 移除了 `get_browser()` 和 `add_wsgi_intercept()` 等函数
- **解决**: 使用 `TwillBrowser()` 类和独立的 `wsgi-intercept` 包

### 2. Flask 模板渲染参数顺序
- **问题**: `_empty_render(template, context, app)` 参数顺序与 Flask 3.x 的 `_render(app, template, context)` 不一致
- **解决**: 修正为 `_empty_render(app, template, context)`

### 3. response.location 返回相对路径
- **问题**: Flask 3.x 的 `response.location` 可能返回相对路径而非绝对 URL
- **解决**: 在 `assertRedirects` 中规范化相对路径为绝对 URL

### 4. BaseTestLiveServer 缺少 create_app
- **问题**: 测试基类未实现抽象方法 `create_app()`
- **解决**: 在测试代码中添加实现

### 5. repr() 格式变化
- **问题**: `' '.join(repr(list))` 在 Python 3.13 中会在字符间插入空格
- **解决**: 直接使用 `repr(list)` 而非 `' '.join(repr(list))`

## 移除的依赖

- `simplejson` - Python 2.6 以下需要，现已内置
- `multiprocessing` - Python 2.6 以下需要，现已内置
- Python 2 特定的 twill 版本锁定

## 备注

所有测试均已通过，项目成功升级到 Python 3.13 + 最新依赖。主要变更集中在：
1. twill 库的 API 适配
2. Flask 3.x 的模板渲染接口变化
3. 测试代码的 bug 修复

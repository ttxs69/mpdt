# 发版

一条命令，从干净的工作区到 PyPI。

```bash
./scripts/release.sh --dry-run          # 构建 + 校验，不发
./scripts/release.sh patch              # 0.2.7 → 0.2.8
./scripts/release.sh minor              # 0.2.7 → 0.3.0
./scripts/release.sh major              # 0.2.7 → 1.0.0
./scripts/release.sh --version 0.3.0rc1
```

## 三步，顺序不能换

| 步骤 | 做什么 | 为什么在这一步 |
|---|---|---|
| **1. 安全检查** | 工作区干净、在 `main`、与 `origin/main` 同步、整条质量门绿 | 脏树发版会把未审的改动打进包里；门不绿时发的版本没人验过 |
| **2. 准备** | 改版本号（两处）、写 CHANGELOG 段、跑 `version_gate` | 版本号不一致的包会以错误的名字发布 |
| **3. 发布** | commit → 打 tag → push，CI 构建并发布 | 发布由 **tag** 触发，不由合并触发 |

这一顺序来自 [OpenJS Foundation 的 secure-release 指南](https://github.com/openjs-foundation/security-wg/blob/main/docs/Secure_Releases/secure-releases.md)：

> *"don't make a new release automatically, but make sure that a human has intentionally said 'it's time to publish a new version, here's the version number it will use'."*

## 版本号在两处，gate 是裁判

`pyproject.toml` 和 `mpdt/__init__.py` 各有一个版本号。`tools/version_gate.py` 检查三件事：

1. 两处一致
2. 不低于现有的任何 tag（不许回退）
3. 推 tag 时，**tag 名必须等于版本号**

第 3 条是关键的：包从源码树构建，tag 是另一个对象。`v0.2.8` 的 tag 指向一棵写着 `0.2.7` 的树，会发布 0.2.7 而标签写着 v0.2.8 —— 而且没有任何环节会发现。

`release.sh` 改完两处版本号后会再跑一次 `version_gate.py`，不合格就不提交。

## CI 做什么（`.github/workflows/publish.yml`）

```
test-first  →  build  →  publish
（全矩阵）     （构建+校验）  （仅 tag，OIDC）
```

`build` 这一关有四件事，`twine check` 只是第一件：

1. **`twine check --strict`** — 元数据合法
2. **`tools/verify_artifact.py`** — 文件本身对不对：
   - METADATA 的版本 == 要发布的版本
   - `mpdt/__init__.py` 的 `__version__` == 要发布的版本
   - wheel 里没有 `tests/`、`tools/`、`__pycache__`、`.pyc`
   - sdist 与 wheel 的 `mpdt/**/*.py` 逐字节相同
   - wheel 不缺 sdist 有的模块
3. **干净 venv 装 wheel 跑一遍** — 装源码树能跑，不等于装产物能跑
4. **`mpdt --version` 必须报出要发布的版本**

`twine check` 全部通过而文件是错的那种包，第 2、3 条才拦得住。

## 手动触发是彩排，不是发布

`workflow_dispatch` 会跑 `test-first` → `build`（含全部校验），然后**停**。
`publish` job 带 `if: github.ref_type == 'tag'`，所以手动触发永远不会上传。

## 凭据：仓库里一个都没有

发布走 PyPI 的 [trusted publishing](https://docs.pypi.org/trusted-publishers/)（OIDC）：
这个 workflow 用 CI 提供者的身份向 PyPI 换一个短期凭据，仓库里没有 API token，
也没有 `~/.pypirc`。每次上传带 attestation。

`permissions:` 每个 job 单独声明，只有 `publish` 拿 `id-token: write`，
其余是 `contents: read`。

## 发版后验证

```bash
gh run list --workflow publish.yml          # 应该全绿
python -m pip index versions mpdt           # 新版本出现
python -m pip install --upgrade mpdt && mpdt --version
```

PyPI 的页面会显示 provenance 徽章，点进去是这次构建的 attestation。

## 仓库保护（一次性设置，已生效）

| 控制 | 现状 |
|---|---|
| `main` 分支 | 需要 PR + 所有检查通过；禁 force push；禁删除；管理员也受限 |
| `v*` tag | GitHub ruleset：禁止创建/更新/删除（不可变） |
| Actions | 全部钉 commit SHA，Dependabot 每周提 PR 升级 |
| 依赖 | `ruff`/`mypy`/`pytest` 等钉在 `requirements-dev.txt` |

钉 SHA 而不是 `@v4` 的理由：一个 tag 可以被移动，一个 commit SHA 不能。
OpenSSF Scorecard 的 *Pinned-Dependencies* 检查的就是这个。

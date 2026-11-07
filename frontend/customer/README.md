# 咪游社前端交接说明

本仓库的运行方式见[双端演示说明](../../README.md)。以下为交接时的原型说明。

这是可运行的 React + Vite 前端源码，包含首页、六步创建活动、需求确认、AI 团队工作台、三套方案比较及详情页面。`首页预览.png` 是最新的桌面首页效果。

## 本地运行

在本文件夹打开终端，执行：

```bash
npm install
npm run dev
```

终端会显示本地预览地址。构建使用 `npm run build`；网页静态文件输出到 `dist/client`。若由后端服务托管，请将非静态资源的前端路由回退到 `index.html`。项目内还保留了原型托管所需的 `worker/`、`.openai/` 和 `scripts/prepare-sites-build.mjs`，因此原有构建命令可直接使用。

## 当前实现边界

- 目前没有接入真实后端、AI Agent、用户登录、预订或支付。
- 活动和草稿存于浏览器 `localStorage`（`src/storage.js`，键名 `miyo-studio-v1`）。换浏览器或设备不会同步。
- 三套活动方案、预算分配和日程由 `src/domain.js` 的 `createPlans()` 本地生成，都是演示数据，不是真实报价或预订结果。
- 工作台进度为前端演示状态，见 `src/App.jsx`。
- 右上角“登录”目前只显示即将开放提示。

## 后端对接位置

- `src/App.jsx`：页面路由、六步表单、草稿保存、活动创建、工作台和方案选择。活动创建逻辑在 `generate()`，可改为调用创建活动接口。
- `src/domain.js`：`blankBrief()` 定义需求字段；`createPlans()` 给出目前三方案数据结构，可用接口响应替换。
- `src/storage.js`：本地草稿与活动记录存储，接入账号后可替换为服务端持久化。
- 主要页面路径：`/`、`/cases`、`/about`、`/activities`、`/create/1` 至 `/create/6`、`/create/review`、`/workspace/:id`、`/activities/:id/plans`、`/activities/:id/plans/:planId`。

需求对象字段：`name` 活动名称、`people` 人数、`team` 团队类型、`city` 出发城市、`destination` 目的地、`date` 日期、`flexible` 时间是否灵活、`duration` 时长、`budget` 人均预算、`goals` 偏好数组、`notes` 补充说明。当前表单中人数和预算以字符串保存，对接接口时建议统一为数值。

建议双方先确认创建活动、读取活动/草稿、获取工作进度、获取三套方案、选择方案、登录与身份验证的接口及响应结构。接口路径目前尚未约定；请勿把本地演示数据视为真实后台结果。

## 素材与范围

页面素材在 `public/assets/`，字体许可证为 `public/assets/OFL.txt`。压缩包未包含 `node_modules` 和构建缓存，收到后运行 `npm install` 即可。当前设计以电脑 16:9 画面为主。

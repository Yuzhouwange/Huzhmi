# Huzhmi Android

独立 Android 版抖音 / TikTok / B 站视频与图集下载工具，不依赖电脑。

## 下载

Android 1.15 安装包：

```
releases/Huzhmi-Android-1.15.apk
```

也可以直接使用 GitHub 原始文件地址安装。首次安装需要在系统里允许“安装未知来源应用”。

## 功能

- 抖音单条视频 / 图集
- TikTok 单条视频 / 图集
- B 站单条视频 / 分 P
- 抖音、TikTok、B 站短链
- 多条链接自动识别、逐条解析、列表展示
- 视频封面 / 图集首图缩略图
- 图集逐张预览、勾选、全选、下载
- 标题 / 文案复制
- 视频下载后预览，可删除
- 抖音 / B 站官方二维码登录
- 需要短信验证码时，二维码下方自动出现输入框
- 二维码保存到相册
- 手动 Cookie、清除 Cookie

## 保存位置

- 视频：`电影/Huzhmi`
- 图片：`图片/Huzhmi`

## 环境

- 最低 Android 7.0（API 24）
- 目标 Android 14（API 34）
- JDK 17
- Android SDK：platform 34、build-tools 34.0.0
- 第三方库：ZXing core 3.5.3（`libs/`）

## 构建

源码是纯 Java + 原生 Android SDK，不使用 Gradle。

```bash
# 1. 资源
aapt2 compile --dir res -o build/res.zip
aapt2 link -o build/base.apk \
  -I "$ANDROID_SDK/platforms/android-34/android.jar" \
  --manifest AndroidManifest.xml \
  -R build/res.zip

# 2. Java
javac -source 8 -target 8 -encoding UTF-8 \
  -classpath "$ANDROID_SDK/platforms/android-34/android.jar:libs/zxing-core-3.5.3.jar" \
  -d build/classes src/com/huzhmi/app/*.java

# 3. dex
d8 --lib "$ANDROID_SDK/platforms/android-34/android.jar" \
  --min-api 24 --output build/dex \
  libs/zxing-core-3.5.3.jar build/classes/com/huzhmi/app/*.class

# 4. 打包、对齐、签名
jar --update --file build/base.apk -C build/dex classes.dex
zipalign -f 4 build/base.apk build/aligned.apk
apksigner sign --ks "$KEYSTORE" --out Huzhmi.apk build/aligned.apk
```

Windows 下把 `:` 改成 `;`，路径按本机 SDK 调整。

## 签名

仓库不包含签名密钥、APK、构建产物或任何本机路径。

需要发布时自行生成密钥：

```bash
keytool -genkeypair -v -keystore release.jks \
  -alias huzhmi -keyalg RSA -keysize 2048 -validity 10000
```

## 隐私

- Cookie 只保存在应用私有 `SharedPreferences`
- 不写日志、不上传服务器
- 仓库不包含用户 Cookie、手机号、验证码、账号信息

## 免责声明

仅用于下载自己有权保存的内容。使用者需遵守平台条款和当地法律。

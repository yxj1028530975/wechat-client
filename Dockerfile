# 使用 Python 3.11 slim 镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 复制依赖文件
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY main.py .
COPY bridge_wechat.py .
COPY wechat_whitelist.txt .

# 创建图片下载目录
RUN mkdir -p /tmp/wechat_images

# 暴露端口
EXPOSE 9000

# 启动命令
CMD ["python", "main.py"]

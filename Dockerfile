FROM lx-music-api-server-base:latest

WORKDIR /app

# 复制项目文件
COPY . .

# 暴露端口
EXPOSE 9763

# 启动服务
CMD ["python", "main.py"]
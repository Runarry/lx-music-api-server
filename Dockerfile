FROM lx-music-api-server-base:latest

WORKDIR /app



# 暴露端口
EXPOSE 9763

# 启动服务
CMD ["python", "main.py"]
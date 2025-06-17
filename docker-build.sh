#!/bin/bash

# 构建基础镜像
echo "=== 构建基础镜像 ==="
docker-compose --profile build-base build

# 构建并启动服务
echo "=== 构建并启动服务 ==="
docker-compose up -d

echo "=== 完成 ==="
echo "服务已在 http://localhost:9763 启动"
# LX Music API Server Docker 部署指南

本文档提供了使用 Docker 和 Docker Compose 部署 LX Music API Server 的详细说明。

## 前提条件

- 已安装 [Docker](https://docs.docker.com/get-docker/)
- 已安装 [Docker Compose](https://docs.docker.com/compose/install/) (使用 Docker Compose 方式部署时需要)

## 使用 Docker Compose 部署（推荐）

1. 克隆仓库或下载源码到本地

```bash
git clone https://github.com/your-username/lx-music-api-server.git
cd lx-music-api-server
```

2. 启动服务

```bash
docker-compose up -d
```

3. 查看日志

```bash
docker-compose logs -f
```

4. 停止服务

```bash
docker-compose down
```

## 使用 Docker 直接部署

1. 构建 Docker 镜像

```bash
docker build -t lx-music-api-server .
```

2. 运行容器

```bash
docker run -d \
  --name lx-music-api-server \
  -p 9763:9763 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/audio:/app/audio \
  lx-music-api-server
```

3. 查看日志

```bash
docker logs -f lx-music-api-server
```

4. 停止容器

```bash
docker stop lx-music-api-server
docker rm lx-music-api-server
```

## 配置说明

服务启动后，默认监听 9763 端口。您可以通过修改 `config` 目录下的配置文件来自定义服务设置。

### 目录挂载

- `/app/config`: 配置文件目录
- `/app/audio`: 音频文件目录

## 注意事项

1. 首次启动时，系统会自动创建默认配置文件
2. 如需修改端口，请同时修改 docker-compose.yml 中的端口映射和配置文件中的端口设置
3. 在 Linux 环境下，可能需要调整挂载目录的权限

```bash
chmod -R 755 ./config
chmod -R 755 ./audio
```

## 故障排除

如果遇到问题，请检查：

1. Docker 和 Docker Compose 是否正确安装
2. 端口 9763 是否被其他应用占用
3. 查看容器日志以获取详细错误信息

```bash
docker logs lx-music-api-server
```

或

```bash
docker-compose logs
```
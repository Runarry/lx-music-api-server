#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试脚本生成功能
用于验证 lx_script.py 的脚本生成是否正常工作
"""

import sys
import os
import asyncio
from unittest.mock import MagicMock
import tempfile
import json

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# 模拟必要的依赖
class MockConfig:
    def __init__(self):
        self.config_data = {
            'security.key.enable': False,
            'security.key.values': ['test_key'],
            'common.ssl_info.is_https': False,
            'common.download_config.name': 'Test Music Source',
            'common.download_config.intro': 'Test description',
            'common.download_config.author': 'Test Author',
            'common.download_config.version': '1.0.0',
            'common.download_config.dev': True,
            'common.download_config.update': True,
            'common.download_config.quality': {
                'kw': ['128k', '320k'],
                'kg': ['128k', '320k'],
                'tx': ['128k'],
                'wy': ['128k'],
                'mg': ['128k']
            },
            'common.download_config.filename': 'test_music_source.js',
            'common.download_config.updateMsg': '发现新版本，请更新: {updateUrl}'
        }
    
    def read_config(self, key):
        return self.config_data.get(key)

class MockRequest:
    def __init__(self, host='localhost:9763', key=None, check_update=None):
        self.host = host
        self.query = {}
        if key:
            self.query['key'] = key
        if check_update:
            self.query['checkUpdate'] = check_update
    
    def get_query(self):
        return MockQuery(self.query)

class MockQuery:
    def __init__(self, query_dict):
        self.query_dict = query_dict
    
    def get(self, key, default=None):
        return self.query_dict.get(key, default)

# 模拟日志
class MockLogger:
    def info(self, msg):
        print(f"[INFO] {msg}")
    
    def warning(self, msg):
        print(f"[WARNING] {msg}")
    
    def error(self, msg):
        print(f"[ERROR] {msg}")

def test_script_generation():
    """测试脚本生成功能"""
    print("=" * 60)
    print("开始测试脚本生成功能")
    print("=" * 60)
    
    # 模拟导入
    from common import lx_script
    
    # 替换依赖
    lx_script.config = MockConfig()
    lx_script.logger = MockLogger()
    
    # 创建模拟的HTTP请求
    mock_request = MockRequest(host='test.example.com:9763', key='test_key')
    mock_request.query = MockQuery({'key': 'test_key'})
    
    async def run_test():
        try:
            # 测试脚本内容获取
            print("1. 测试脚本内容获取...")
            script_content = lx_script.get_script_content()
            
            if script_content is None:
                print("❌ 无法获取脚本内容")
                return False
            else:
                print("✅ 成功获取脚本内容")
                print(f"   脚本长度: {len(script_content)} 字符")
                print(f"   脚本开头: {script_content[:100]}...")
            
            # 测试脚本生成响应
            print("\n2. 测试脚本生成响应...")
            response = await lx_script.generate_script_response(mock_request)
            
            # 检查响应类型
            if hasattr(response, 'text'):
                # 是 Response 对象
                generated_script = response.text
                print("✅ 成功生成脚本响应")
                print(f"   Content-Type: {response.content_type}")
                
                # 检查文件名
                if 'Content-Disposition' in response.headers:
                    print(f"   文件名: {response.headers['Content-Disposition']}")
                
            else:
                # 是错误响应
                error_data, status_code = response
                print(f"❌ 生成脚本失败: {error_data}")
                return False
            
            # 验证生成的脚本内容
            print("\n3. 验证生成的脚本内容...")
            
            # 检查API_URL是否被正确替换
            expected_api_url = 'const API_URL = \'http://test.example.com:9763\''
            if expected_api_url in generated_script:
                print("✅ API_URL 替换正确")
            else:
                print("❌ API_URL 替换失败")
                # 查找实际的API_URL行
                api_url_lines = [line for line in generated_script.split('\n') if 'API_URL' in line and 'const' in line]
                if api_url_lines:
                    print(f"   实际的API_URL行: {api_url_lines[0].strip()}")
                else:
                    print("   未找到API_URL行")
                return False
            
            # 检查API_KEY是否被正确替换
            expected_api_key = 'const API_KEY = \'test_key\''
            if expected_api_key in generated_script:
                print("✅ API_KEY 替换正确")
            else:
                print("❌ API_KEY 替换失败")
                # 查找实际的API_KEY行
                api_key_lines = [line for line in generated_script.split('\n') if 'API_KEY' in line and 'const' in line]
                if api_key_lines:
                    print(f"   实际的API_KEY行: {api_key_lines[0].strip()}")
                else:
                    print("   未找到API_KEY行")
                return False
            
            # 检查配置信息是否被正确替换
            if '* @name Test Music Source' in generated_script:
                print("✅ 源名称替换正确")
            else:
                print("❌ 源名称替换失败")
                return False
            
            # 检查其他配置信息
            if '* @description Test description' in generated_script:
                print("✅ 源描述替换正确")
            else:
                print("❌ 源描述替换失败")
                return False
                
            if '* @version 1.0.0' in generated_script:
                print("✅ 版本替换正确")
            else:
                print("❌ 版本替换失败")
                return False
                
            if '* @author Test Author' in generated_script:
                print("✅ 作者替换正确")
            else:
                print("❌ 作者替换失败")
                return False
            
            # 检查音质配置是否被正确替换
            expected_quality_config = lx_script.config.read_config('common.download_config.quality')
            print(f"   期望的音质配置: {json.dumps(expected_quality_config)}")
            
            # 查找生成脚本中的音质配置
            import re
            # 现在模板直接使用JSON对象，不再使用JSON.parse
            music_quality_match = re.search(r'const MUSIC_QUALITY = ({[^}]+})', generated_script)
            if music_quality_match:
                actual_quality_json = music_quality_match.group(1)
                print(f"   实际的音质配置: {actual_quality_json}")
                
                try:
                    # 解析实际的JSON字符串
                    actual_quality_config = json.loads(actual_quality_json)
                    
                    # 比较两个字典
                    if actual_quality_config == expected_quality_config:
                        print("✅ 音质配置替换正确")
                    else:
                        print("❌ 音质配置替换失败")
                        print(f"   期望: {expected_quality_config}")
                        print(f"   实际: {actual_quality_config}")
                        return False
                except json.JSONDecodeError as e:
                    print(f"❌ 音质配置JSON解析失败: {e}")
                    return False
            else:
                print("❌ 未找到音质配置")
                # 查看脚本中是否有MUSIC_QUALITY
                if 'MUSIC_QUALITY' in generated_script:
                    music_quality_lines = [line for line in generated_script.split('\n') if 'MUSIC_QUALITY' in line]
                    for line in music_quality_lines:
                        print(f"   找到的MUSIC_QUALITY行: {line.strip()}")
                return False
            
            # 检查info参数注入
            if 'const infoPayload = utils.buffer.bufToString' in generated_script:
                print("✅ info参数注入正确")
            else:
                print("❌ info参数注入失败")
                return False
            
            # 检查URL修改
            if '`${API_URL}/url/${source}/${songId}/${quality}?info=${infoPayload}`' in generated_script:
                print("✅ URL参数修改正确")
            else:
                print("❌ URL参数修改失败")
                return False
            
            return generated_script
            
        except Exception as e:
            print(f"❌ 测试过程中发生错误: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # 运行异步测试
    result = asyncio.run(run_test())
    
    if result:
        print("\n" + "=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        return result
    else:
        print("\n" + "=" * 60)
        print("❌ 测试失败！")
        print("=" * 60)
        return None

def save_generated_script(script_content, output_dir="./test_output"):
    """保存生成的脚本到指定目录"""
    if not script_content:
        print("❌ 没有脚本内容可保存")
        return False
    
    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存脚本文件
    output_file = os.path.join(output_dir, "generated_music_source.js")
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        print(f"✅ 脚本已保存到: {os.path.abspath(output_file)}")
        print(f"   文件大小: {len(script_content)} 字符")
        
        return True
    except Exception as e:
        print(f"❌ 保存脚本失败: {e}")
        return False

if __name__ == "__main__":
    print("开始测试脚本生成功能...")
    
    # 运行测试
    generated_script = test_script_generation()
    
    if generated_script:
        # 保存生成的脚本
        print("\n保存生成的脚本...")
        save_generated_script(generated_script)
    
    print("\n测试完成！") 
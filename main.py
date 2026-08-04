import os
import sys
import json
import requests
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from requests.auth import HTTPDigestAuth
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import time
from typing import Dict, Any, Optional, Union

# Tải biến môi trường từ file .env nằm cạnh exe (không bundle vào exe)
if getattr(sys, 'frozen', False):
    ENV_DIR = os.path.dirname(sys.executable)
else:
    ENV_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ENV_DIR, '.env'))

# Cấu hình từ file .env
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
API_BASE_URL = os.getenv('API_BASE_URL', 'https://trial.utilicore.net/API2.ashx')
API_USERNAME = os.getenv('API_USERNAME')
API_PASSWORD = os.getenv('API_PASSWORD')
TIME_RANGE_HOURS = int(os.getenv('TIME_RANGE_HOURS', '1'))
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'
API_TIMEOUT = int(os.getenv('API_TIMEOUT', '30'))
API_RETRY_ATTEMPTS = int(os.getenv('API_RETRY_ATTEMPTS', '3'))

# Kiểm tra các biến bắt buộc
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not found in .env")
if not API_BASE_URL:
    raise ValueError("API_BASE_URL not found in .env")
if not API_USERNAME:
    raise ValueError("API_USERNAME not found in .env")
if not API_PASSWORD:
    raise ValueError("API_PASSWORD not found in .env")

# Cấu hình logging
log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=log_level
)
logger = logging.getLogger(__name__)

# Tạo session với Digest Authentication
class APIClient:
    def __init__(self):
        self.session = requests.Session()
        self.auth = HTTPDigestAuth(API_USERNAME, API_PASSWORD)
        self.base_url = API_BASE_URL.rstrip('/')
        self.timeout = API_TIMEOUT
        self.retry_attempts = API_RETRY_ATTEMPTS
        
    def make_request(self, endpoint: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Thực hiện request với retry logic"""
        url = f"{self.base_url}/{endpoint}?{json.dumps(payload)}"
        
        for attempt in range(self.retry_attempts):
            try:
                logger.debug(f"API Request: {endpoint} (attempt {attempt + 1}/{self.retry_attempts})")
                
                response = self.session.get(
                    url,
                    auth=self.auth,
                    timeout=self.timeout,
                    headers={
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    }
                )
                
                logger.debug(f"Response status: {response.status_code}")
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        logger.info(f"API {endpoint} success")
                        return data
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}")
                        return {"error": f"Invalid JSON response: {str(e)}", "raw": response.text[:500]}
                
                elif response.status_code == 401:
                    logger.error(f"Authentication failed (401) for {endpoint}")
                    return {"error": "Authentication failed - Invalid username or password", "status_code": 401}
                
                elif response.status_code == 403:
                    logger.error(f"Access forbidden (403) for {endpoint}")
                    return {"error": "Access forbidden - Insufficient permissions", "status_code": 403}
                
                elif response.status_code == 404:
                    logger.error(f"Endpoint not found (404): {endpoint}")
                    return {"error": "Endpoint not found", "status_code": 404}
                
                else:
                    logger.warning(f"API returned {response.status_code} for {endpoint}")
                    if attempt < self.retry_attempts - 1:
                        wait_time = 2 ** attempt
                        logger.debug(f"Waiting {wait_time}s before retry...")
                        time.sleep(wait_time)
                    else:
                        return {
                            "error": f"API request failed with status {response.status_code}",
                            "status_code": response.status_code,
                            "response": response.text[:500]
                        }
                        
            except requests.exceptions.Timeout:
                logger.error(f"Timeout when calling API {endpoint} (attempt {attempt + 1})")
                if attempt < self.retry_attempts - 1:
                    continue
                return {"error": "Request timeout - Server did not respond in time"}
                
            except requests.exceptions.ConnectionError:
                logger.error(f"Connection error when calling API {endpoint} (attempt {attempt + 1})")
                if attempt < self.retry_attempts - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {"error": "Connection error - Cannot connect to server"}
                
            except Exception as e:
                logger.error(f"Unexpected error when calling API {endpoint}: {e}")
                return {"error": f"Unexpected error: {str(e)}"}
        
        return {"error": f"Failed after {self.retry_attempts} attempts"}

# Khởi tạo API client
api_client = APIClient()

def has_data(data_result) -> bool:
    """Check GetDataMulti result has at least one non-empty stream"""
    if not data_result or "error" in data_result:
        return False
    return any(isinstance(v, list) and len(v) > 0 for v in data_result.values())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gửi tin nhắn chào mừng khi người dùng bắt đầu."""
    await update.message.reply_text(
        "Bot API Integration\n\n"
        "Usage Guide:\n"
        "• R<site_id> - Get site information\n"
        "• D<site_id> - Get channel data\n\n"
        "Examples:\n"
        "• R123 - Get info for site ID 123\n"
        "• D456 - Get data from site ID 456\n\n"
        f"Time range: {TIME_RANGE_HOURS} hour(s)"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tin nhắn từ người dùng - PLAIN TEXT ONLY"""
    user_message = update.message.text.strip()
    user_id = update.effective_user.id
    
    logger.info(f"Received from user_id={user_id}: {user_message}")
    
    # Kiểm tra định dạng tin nhắn
    if len(user_message) < 2:
        await update.message.reply_text("Invalid format! Use R123 or D456")
        return
    
    command_type = user_message[0].upper()
    site_id = user_message[1:]
    
    # Kiểm tra site_id có phải là số không
    if not site_id.isdigit():
        await update.message.reply_text("Site ID must be a number! Examples: R123 or D456")
        return
    
    # Gửi tin nhắn đang xử lý
    processing_msg = await update.message.reply_text(f"Processing {user_message}...")
    
    try:
        if command_type == 'R':
            result = await get_site_info(site_id)
        elif command_type == 'D':
            result = await get_channel_data(site_id)
        else:
            result = "Command not supported! Only R (site info) and D (channel data)"
        
        # Xóa tin nhắn đang xử lý
        try:
            await processing_msg.delete()
        except:
            pass
        
        # GỬI PLAIN TEXT - CHIA NHỎ NẾU CẦN
        await send_plain_text(update.effective_chat.id, result, context)
        
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        
        try:
            await processing_msg.delete()
        except:
            pass
        
        await update.message.reply_text(f"Error: {str(e)}")

async def send_plain_text(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE):
    """Gửi plain text, tự động chia nhỏ nếu dài"""
    max_length = 4000  # Để an toàn, dùng 4000 thay vì 4096
    
    if len(text) <= max_length:
        await context.bot.send_message(chat_id=chat_id, text=text)
        return
    
    # Chia thành nhiều phần
    parts = split_text(text, max_length)
    
    for i, part in enumerate(parts):
        if len(parts) > 1:
            header = f"Part {i+1}/{len(parts)}\n\n"
        else:
            header = ""
        
        await context.bot.send_message(chat_id=chat_id, text=header + part)
        
        # Delay nhẹ giữa các tin nhắn
        if i < len(parts) - 1:
            import asyncio
            await asyncio.sleep(0.3)

def split_text(text: str, max_length: int) -> list:
    """Chia text thành các phần nhỏ"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    current_part = ""
    
    # Tách theo dòng
    lines = text.split('\n')
    
    for line in lines:
        if len(current_part) + len(line) + 1 > max_length:
            if current_part:
                parts.append(current_part)
            current_part = line
        else:
            if current_part:
                current_part += '\n' + line
            else:
                current_part = line
    
    if current_part:
        parts.append(current_part)
    
    return parts

async def get_site_info(site_id: str):
    """Gọi API để lấy thông tin site."""
    try:
        payload = {"siteId": int(site_id)}
        data = api_client.make_request("GetSite", payload)
        
        if data and "error" in data:
            error_msg = data.get("error", "Unknown error")
            status_code = data.get("status_code", "N/A")
            
            if status_code == 401:
                return ("Authentication Failed!\n\n"
                       "Please check:\n"
                       "1. Username and password in .env file\n"
                       "2. API supports Digest Authentication\n"
                       "3. Account has API access permissions")
            elif status_code == 403:
                return "Access Forbidden! You don't have permission to access this resource"
            elif status_code == 404:
                return f"Site not found! Site ID {site_id} was not found"
            else:
                return f"API Error: {error_msg}\nStatus Code: {status_code}"
        
        if not data:
            return f"No data returned for site ID {site_id}"
        
        # Format đơn giản
        formatted = format_site_data_simple(data)
        return f"Site Information {site_id}:\n{formatted}"
    
    except Exception as e:
        logger.error(f"Error in get_site_info: {e}")
        return f"Error occurred: {str(e)}"

def format_site_data_simple(data):
    """Format site data đơn giản cho plain text"""
    try:
        result = []
        
        # Basic info
        if 'SiteID' in data:
            result.append(f"SiteID: {data['SiteID']}")
        if 'SiteName' in data:
            result.append(f"Site Name: {data['SiteName']}")
        # if 'TelephoneNumber' in data:
        #     result.append(f"Telephone Number: {data['TelephoneNumber']}")
        # if 'Location' in data:
        #     result.append(f"Location: {data['Location']}")
        # if 'Comment' in data:
        #     result.append(f"Comment: {data['Comment']}")
        # if 'Version' in data:
        #     result.append(f"Version: {data['Version']}")

        stream_ids = []
            
        # Channels
        if 'Channels' in data and data['Channels']:
            for channel in data['Channels']:
                channel_number = channel.get('Number')
                if channel_number:
                    stream_id = f"{data.get('SiteID', '')}_{channel_number}"
                    if stream_id and '_' in stream_id:
                        stream_ids.append(stream_id)
            
            if not stream_ids:
                return "No streamID found in channels"
            
            print(stream_ids)
            
            # Bước 3: Tính toán thời gian
            now = datetime.now()
            end_time = int(now.timestamp() * 1000)
            start_time = end_time - (TIME_RANGE_HOURS * 3600 * 1000 * 15)
            
            # Cộng 8 giờ cho API
            start_time_for_api = start_time + (8 * 3600 * 1000)
            end_time_for_api = end_time + (24 * 3600 * 1000)
            
            # Bước 4: Gọi API GetDataMulti
            data_payload = {
                "streamIds": stream_ids,
                "start": start_time_for_api,
                "end": end_time_for_api
            }

            data_result = api_client.make_request("GetDataMulti", data_payload)

            if not has_data(data_result):
                # Retry với start_time lùi 1 năm
                start_1year_for_api = (end_time - (365 * 24 * 3600 * 1000)) + (8 * 3600 * 1000)
                data_result = api_client.make_request("GetDataMulti", {
                    "streamIds": stream_ids,
                    "start": start_1year_for_api,
                    "end": end_time_for_api
                })
                if not has_data(data_result):
                    data_result = {}

            result.append("\nChannels:")
            for i, channel in enumerate(data['Channels'][:10]):  # Giới hạn 10 channels
                if "TGSN" not in channel['Name'] and "Messages" not in channel['Name'] and "Angle"  not in channel['Name'] and "Message" not in channel['Name'] and "Signal" not in channel['Name']:
                    # channel_info = []
                    # if 'Name' in channel:
                    #     channel_info.append(f"Name: {channel['Name']}")
                    # if 'Number' in channel:
                    #     channel_info.append(f"Number: {channel['Number']}")
                    # if 'Units' in channel:
                    #     channel_info.append(f"Units: {channel['Units']}")
                    # if 'LastData' in channel:
                    #     # Format LastData với -7 giờ
                    #     last_data = format_lastdata_simple(channel['LastData'])
                    #     channel_info.append(f"LastData: {last_data}")

                    channel_id = f"{data['SiteID']}_{channel['Number']}";
                    v = None
                    check = True

                    for stream_id, d in data_result.items():
                        if channel_id in stream_id:
                            if len(d) > 1:
                                v = d[len(d) - 1]['v']
                                v = f"{v:.2f}"
                                v = str(v)
                            else: 
                                v = ""
                                check = False
                            break
                    if check == True:
                        result.append(f"  {i+1}. {channel['Name']} ({channel['Number']}): {format_lastdata_simple(channel['LastData'])} - {v} {channel['Units']}")
            
            # if len(data['Channels']) > 10:
            #     result.append(f"  ... and {len(data['Channels']) - 10} more channels")
        
        return '\n'.join(result)
    except Exception as e:
        logger.error(f"Error formatting site data: {e}")
        return str(data)

def format_lastdata_simple(lastdata_value):
    """Format LastData đơn giản - trừ 7 giờ"""
    try:
        if not lastdata_value or lastdata_value in ["N/A", "null", "None", ""]:
            return "N/A"
        
        # Nếu là chuỗi số (timestamp milliseconds)
        if isinstance(lastdata_value, str) and lastdata_value.replace('.', '').isdigit():
            try:
                ts = float(lastdata_value)
                if ts > 1000000000000:  # Valid timestamp
                    dt = datetime.fromtimestamp(ts / 1000)
                    dt_minus_7 = dt - timedelta(hours=8)
                    return dt_minus_7.strftime('%d/%m/%Y %H:%M:%S')
            except:
                return str(lastdata_value)
        
        # Nếu là số (int/float)
        elif isinstance(lastdata_value, (int, float)):
            ts = float(lastdata_value)
            if ts > 1000000000000:  # Valid timestamp
                dt = datetime.fromtimestamp(ts / 1000)
                dt_minus_7 = dt - timedelta(hours=8)
                return dt_minus_7.strftime('%d/%m/%Y %H:%M:%S')
        
        # Nếu là chuỗi date
        elif isinstance(lastdata_value, str):
            try:
                dt = datetime.strptime(lastdata_value, '%d/%m/%Y %H:%M:%S')
                dt_minus_7 = dt - timedelta(hours=8)
                return dt_minus_7.strftime('%d/%m/%Y %H:%M:%S')
            except ValueError:
                try:
                    dt = datetime.strptime(lastdata_value, '%Y-%m-%d %H:%M:%S')
                    dt_minus_7 = dt - timedelta(hours=8)
                    return dt_minus_7.strftime('%d/%m/%Y %H:%M:%S')
                except:
                    return str(lastdata_value)
        
        return str(lastdata_value)
    except:
        return str(lastdata_value)

async def get_channel_data(site_id: str):
    """Lấy dữ liệu từ site, sau đó lấy dữ liệu từ channels."""
    try:
        # Bước 1: Lấy thông tin site
        site_payload = {"siteId": int(site_id)}
        site_data = api_client.make_request("GetSite", site_payload)
        
        if site_data and "error" in site_data:
            error_msg = site_data.get("error", "Unknown error")
            return f"Error getting site info: {error_msg}"
        
        # Bước 2: Lấy danh sách streamID từ channels
        channels = site_data.get('Channels', [])
        if not channels:
            return "No channels found in site"
        
        stream_ids = []
        channel_info = {}  # Lưu thông tin channel để hiển thị
        
        for channel in channels:
            channel_number = channel.get('Number')
            if channel_number:
                stream_id = f"{site_data.get('SiteID', '')}_{channel_number}"
                if stream_id and '_' in stream_id:
                    stream_ids.append(stream_id)
                    # Lưu thông tin channel
                    channel_info[stream_id] = {
                        'name': channel.get('Name', f'Channel {channel_number}'),
                        'number': channel_number,
                        'units': channel.get('Units', '')
                    }
        
        if not stream_ids:
            return "No streamID found in channels"
        
        logger.info(f"Found {len(stream_ids)} streamIDs in site {site_id}")
        
        # Bước 3: Tính toán thời gian
        now = datetime.now()
        end_time = int(now.timestamp() * 1000)
        start_time = end_time - (TIME_RANGE_HOURS  * 3600 * 1000 * 15)
        
        # # Format thời gian hiển thị
        # start_time_dt = datetime.fromtimestamp(start_time / 1000)
        # end_time_dt = datetime.fromtimestamp(end_time / 1000)
        
        # start_time_str = start_time_dt.strftime('%d/%m/%Y %H:%M:%S')
        # end_time_str = end_time_dt.strftime('%d/%m/%Y %H:%M:%S')
        
        # Cộng 8 giờ cho API
        start_time_for_api = start_time + (8 * 3600 * 1000)
        end_time_for_api = end_time + (24 * 3600 * 1000)
        
        # Bước 4: Gọi API GetDataMulti
        data_payload = {
            "streamIds": stream_ids,
            "start": start_time_for_api,
            "end": end_time_for_api
        }
        
        logger.info(f"Calling GetDataMulti for {len(stream_ids)} streams, time range: {TIME_RANGE_HOURS} hour(s)")
        
        data_result = api_client.make_request("GetDataMulti", data_payload)

        if data_result and "error" in data_result:
            error_msg = data_result.get("error", "Unknown error")
            return f"Error getting channel data: {error_msg}"

        if not has_data(data_result):
            # Retry với start_time lùi 1 năm
            start_1year_for_api = (end_time - (365 * 24 * 3600 * 1000)) + (8 * 3600 * 1000)
            data_result = api_client.make_request("GetDataMulti", {
                "streamIds": stream_ids,
                "start": start_1year_for_api,
                "end": end_time_for_api
            })
            if data_result and "error" in data_result:
                error_msg = data_result.get("error", "Unknown error")
                return f"Error getting channel data: {error_msg}"
            if not has_data(data_result):
                data_result = {}

        if not data_result:
            return f"No data returned for site {site_id}"
        
        # Bước 5: Xử lý và format dữ liệu - Lấy 5 record mới nhất mỗi stream
        formatted_data = process_and_format_channel_data(data_result, channel_info, max_records=5)
        
        result = (f"Channel Data - Site {site_id}\n"
                # f"Time Range: Last {TIME_RANGE_HOURS} hour(s)\n"
                # f"Local Time: {start_time_str} to {end_time_str}\n"
                # f"Number of Channels: {len(stream_ids)}\n"
                # f"Records per channel: Top 5 (sorted by time descending)\n\n"
                 f"{formatted_data}")
        
        return result
            
    except Exception as e:
        logger.error(f"Error in get_channel_data: {e}", exc_info=True)
        return f"Error occurred: {str(e)}"


def process_and_format_channel_data(data_result, channel_info, max_records=5):
    """Xử lý và format dữ liệu channel, lấy top N record mới nhất"""
    try:
        if not data_result:
            return "No data available"
        
        result_lines = []
        
        # Duyệt qua từng stream trong kết quả
        for stream_id, data in data_result.items():
            if "TGSN" not in channel_info[stream_id]['name'] and "Messages" not in channel_info[stream_id]['name'] and "Angle"  not in channel_info[stream_id]['name'] and "Message" not in channel_info[stream_id]['name'] and "Signal" not in channel_info[stream_id]['name'] and len(data) > 0:
                if stream_id in channel_info:
                    channel_name = channel_info[stream_id]['name']
                    channel_units = channel_info[stream_id]['units']
                else:
                    channel_name = stream_id
                    channel_units = ""
                
                result_lines.append(f"\n{'='*50}")
                result_lines.append(f"Channel: {channel_name} ({stream_id})")
                if channel_units:
                    result_lines.append(f"Units: {channel_units}")
                result_lines.append(f"{'='*50}")
                
                if isinstance(data, list) and len(data) > 0:
                    # Xác định định dạng dữ liệu: array hay dictionary
                    first_item = data[0]
                    
                    if isinstance(first_item, dict):
                        # Định dạng: [{'ts': timestamp, 'v': value, ...}, ...]
                        process_dict_format_data(data, result_lines, max_records)
                    
                    elif isinstance(first_item, list):
                        # Định dạng: [[timestamp, value1, value2, ...], ...]
                        process_array_format_data(data, result_lines, max_records)
                    
                    else:
                        # Định dạng không xác định
                        result_lines.append(f"Unknown data format: {type(first_item)}")
                        limit_data = data[:max_records]
                        for i, record in enumerate(limit_data, 1):
                            result_lines.append(f"  {i}. {record}")
                        
                        # if len(data) > max_records:
                        #     result_lines.append(f"  ... and {len(data) - max_records} more records")
                
                else:
                    result_lines.append(f"Data: {data}")
        
        if len(result_lines) == 0:
            return "No channel data available"
        
        return '\n'.join(result_lines)
        
    except Exception as e:
        logger.error(f"Error processing channel data: {e}")
        return f"Error processing data: {str(e)}"

def process_dict_format_data(data, result_lines, max_records):
    """Xử lý dữ liệu dictionary format: [{'ts': timestamp, 'v': value, ...}, ...]"""
    try:
        # Sắp xếp theo ts giảm dần
        sorted_data = sorted(data, 
                           key=lambda x: x.get('ts', 0) if isinstance(x, dict) else 0, 
                           reverse=True)
        
        top_records = sorted_data[:max_records]
        total_records = len(data)
        
        # result_lines.append(f"Total records: {total_records}")
        # result_lines.append(f"Showing top {len(top_records)} newest records:")
        
        for i, record in enumerate(top_records, 1):
            if isinstance(record, dict):
                # Format timestamp (ts field)
                ts = record.get('ts')
                formatted_time = format_timestamp_simple(ts)
                
                # Lấy các giá trị khác (loại bỏ ts)
                other_values = []
                for key, value in record.items():
                    if key.lower() != 'ts' and key.lower() != 'nodata':
                        if isinstance(value, (int, float)):
                            other_values.append(f"{value:.2f}")
                        else:
                            other_values.append(f"{value}")
                
                values_str = ", ".join(other_values)
                result_lines.append(f"  {i}.{formatted_time} - {values_str}")
            else:
                result_lines.append(f"  {i}. {record}")
        
        # if total_records > max_records:
        #     result_lines.append(f"  ... and {total_records - max_records} older records")
    
    except Exception as e:
        logger.error(f"Error processing dict format data: {e}")
        result_lines.append(f"  Error processing data: {str(e)}")

def process_array_format_data(data, result_lines, max_records):
    """Xử lý dữ liệu array format: [[timestamp, value1, value2, ...], ...]"""
    try:
        # Sắp xếp theo timestamp (phần tử đầu tiên) giảm dần
        sorted_data = sorted(data, 
                           key=lambda x: x[0] if isinstance(x, list) and len(x) > 0 else 0, 
                           reverse=True)
        
        top_records = sorted_data[:max_records]
        total_records = len(data)
        
        # result_lines.append(f"Total records: {total_records}")
        # result_lines.append(f"Showing top {len(top_records)} newest records:")
        
        for i, record in enumerate(top_records, 1):
            if isinstance(record, list) and len(record) > 0:
                # Format timestamp
                timestamp = record[0]
                formatted_time = format_timestamp_simple(timestamp)
                
                # Lấy các giá trị
                values = record[1:] if len(record) > 1 else []
                
                # Format giá trị
                if len(values) == 0:
                    value_str = "No values"
                elif len(values) == 1:
                    value = values[0]
                    if isinstance(value, (int, float)):
                        value_str = f"{value:.4f}"
                    else:
                        value_str = str(value)
                else:
                    formatted_values = []
                    for value in values:
                        if isinstance(value, (int, float)):
                            formatted_values.append(f"{value:.4f}")
                        else:
                            formatted_values.append(str(value))
                    value_str = f"[{', '.join(formatted_values)}]"
                
                result_lines.append(f"  {i}. {formatted_time} - {value_str}")
            else:
                result_lines.append(f"  {i}. {record}")
        
        # if total_records > max_records:
        #     result_lines.append(f"  ... and {total_records - max_records} older records")
    
    except Exception as e:
        logger.error(f"Error processing array format data: {e}")
        result_lines.append(f"  Error processing data: {str(e)}")

def format_channel_data_simple(data):
    """Format channel data đơn giản cho plain text - Format 'ts' property với -7 hours"""
    try:
        if isinstance(data, dict):
            result = []
            for key, value in data.items():
                # Xử lý các field timestamp
                if key.lower() == 'ts' or 'timestamp' in key.lower():
                    # Format timestamp với -7 giờ
                    formatted_ts = format_timestamp_simple(value)
                    result.append(f"{key}: {formatted_ts}")
                elif isinstance(value, list) and len(value) > 0:
                    # Dữ liệu channel
                    result.append(f"\n{key}:")
                    for i, item in enumerate(value[:20]):  # Giới hạn 20 dòng
                        if isinstance(item, list) and len(item) > 0:
                            # Format dòng dữ liệu
                            formatted_row = format_data_row(item)
                            result.append(f"  {formatted_row}")
                        elif isinstance(item, dict):
                            # Format dictionary item
                            formatted_dict = format_dict_item(item)
                            result.append(f"  {formatted_dict}")
                        else:
                            result.append(f"  {item}")
                    
                    if len(value) > 20:
                        result.append(f"  ... and {len(value) - 20} more rows")
                elif isinstance(value, dict):
                    # Format nested dictionary
                    formatted_nested = format_nested_dict(value)
                    result.append(f"{key}:\n{formatted_nested}")
                else:
                    result.append(f"{key}: {value}")
            
            return '\n'.join(result)
        else:
            return str(data)
    except Exception as e:
        logger.error(f"Error formatting channel data: {e}")
        return str(data)

def format_data_row(row):
    """Format một dòng dữ liệu (list)"""
    try:
        if len(row) == 0:
            return "[]"
        
        # Nếu phần tử đầu là timestamp
        if isinstance(row[0], (int, float)) and row[0] > 1000000000000:
            timestamp = format_timestamp_simple(row[0])
            values = row[1:] if len(row) > 1 else []
            return f"{timestamp}: {values}"
        else:
            return str(row)
    except:
        return str(row)

def format_dict_item(item):
    """Format dictionary item, tìm và format các field timestamp"""
    try:
        if not isinstance(item, dict):
            return str(item)
        
        parts = []
        for k, v in item.items():
            # Format timestamp fields
            if k.lower() in ['ts', 'timestamp', 'time', 'date', 'lastdata']:
                formatted = format_timestamp_simple(v)
                parts.append(f"{k}: {formatted}")
            else:
                parts.append(f"{k}: {v}")
        
        return "{" + ", ".join(parts) + "}"
    except:
        return str(item)

def format_nested_dict(data, indent=2):
    """Format nested dictionary với indent"""
    try:
        result = []
        indent_str = " " * indent
        
        for key, value in data.items():
            # Format timestamp fields
            if key.lower() in ['ts', 'timestamp', 'time', 'date', 'lastdata']:
                formatted_value = format_timestamp_simple(value)
                result.append(f"{indent_str}{key}: {formatted_value}")
            elif isinstance(value, dict):
                result.append(f"{indent_str}{key}:")
                nested = format_nested_dict(value, indent + 2)
                result.append(nested)
            elif isinstance(value, list):
                result.append(f"{indent_str}{key}: [")
                for i, item in enumerate(value[:5]):  # Giới hạn 5 items
                    if isinstance(item, dict):
                        nested = format_nested_dict(item, indent + 4)
                        result.append(f"{indent_str}  {i}: {nested}")
                    else:
                        result.append(f"{indent_str}  {i}: {item}")
                if len(value) > 5:
                    result.append(f"{indent_str}  ... and {len(value) - 5} more")
                result.append(f"{indent_str}]")
            else:
                result.append(f"{indent_str}{key}: {value}")
        
        return '\n'.join(result)
    except:
        return str(data)

def format_timestamp_simple(timestamp_ms):
    """Format timestamp đơn giản - trừ 7 giờ"""
    try:
        if not timestamp_ms or timestamp_ms == "N/A":
            return "N/A"
        
        ts = float(timestamp_ms)
        if ts > 1000000000000:
            dt = datetime.fromtimestamp(ts / 1000)
            dt_minus_7 = dt - timedelta(hours=8)
            return dt_minus_7.strftime('%d/%m/%Y %H:%M:%S')
        
        return str(timestamp_ms)
    except:
        return str(timestamp_ms)

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test kết nối"""
    await update.message.reply_text("Testing connection...")
    
    try:
        test_payload = {"siteId": 1}
        result = api_client.make_request("GetSite", test_payload)
        
        if result and "error" in result:
            status_code = result.get("status_code")
            if status_code == 401:
                await update.message.reply_text("Authentication Failed!")
            else:
                await update.message.reply_text(f"Connection OK but error: {result.get('error')}")
        else:
            await update.message.reply_text("Authentication Successful!")
    except Exception as e:
        await update.message.reply_text(f"Test error: {str(e)}")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị trạng thái"""
    now = datetime.now()
    status = (
        f"Bot Status\n"
        f"API URL: {API_BASE_URL}\n"
        f"Username: {API_USERNAME}\n"
        f"Local Time: {now.strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"Time Range: {TIME_RANGE_HOURS} hour(s)\n"
        f"Debug Mode: {DEBUG}"
    )
    await update.message.reply_text(status)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lỗi"""
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text("An error occurred while processing your request.")

def main():
    """Khởi chạy bot"""
    print("=" * 50)
    print("Telegram Bot Starting...")
    print(f"API Base URL: {API_BASE_URL}")
    print(f"Username: {API_USERNAME}")
    #print(f"Time Range: {TIME_RANGE_HOURS} hour(s)")
    print("=" * 50)
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Đăng ký handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
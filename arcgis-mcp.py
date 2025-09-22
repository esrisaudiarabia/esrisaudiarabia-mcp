# mcp_server/server.py

from mcp.server.fastmcp import FastMCP
import asyncio
import logging
import json
import sys
import io
from arcgis.gis import GIS
from typing import Optional, List, Dict, Any
import os
from dotenv import load_dotenv
import datetime
from pathlib import Path
from bs4 import BeautifulSoup

load_dotenv()

def setup_unicode_logging():
    Path("logs").mkdir(exist_ok=True)
    logger = logging.getLogger('ArcGIS_Server')
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.setLevel(logging.INFO)
    detailed_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # Add this condition to prevent console logging when in a tool context
    if os.getenv("MCP_RUNNING_AS_TOOL") != "true":
        try:
            if sys.platform.startswith('win'):
                console_stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
                console_handler = logging.StreamHandler(console_stream)
            else:
                console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(detailed_formatter)
            logger.addHandler(console_handler)
        except Exception as e:
            fallback_handler = logging.StreamHandler()
            fallback_handler.setLevel(logging.INFO)
            fallback_handler.setFormatter(detailed_formatter)
            logger.addHandler(fallback_handler)
            print(f"Warning: Using fallback console handler: {e}")

    # File handler remains the same
    try:
        file_handler = logging.FileHandler('logs/arcgis_server.log', mode='a', encoding='utf-8', errors='replace')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(detailed_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Could not create file handler: {e}")

    return logger

logger = setup_unicode_logging()

mcp = FastMCP(name="ArcGIS_AI_Server")

@mcp.tool(name="search_content", description="Advanced ArcGIS Portal content search.")
def search_content(
    exact_name: Optional[str] = None,
    query: Optional[str] = None,
    item_type: Optional[str] = None,
    owner: Optional[str] = None,
    tags: Optional[Any] = None,
    created_after: Optional[str] = None,
    max_results: int = 10,
    username: Optional[str] = None,
    password: Optional[str] = None,
    portal_url: Optional[str] = None,
    token: Optional[str] = None,
    sort_by: str = "relevance",
    organization_filter: bool = False
) -> str:
    """Execute comprehensive portal content search"""
    operation_id = f"search_{int(datetime.datetime.now().timestamp())}"
    all_params = {k:v for k,v in locals().items() if k not in ['password', 'token']}
    logger.info(f"[INFO] Starting search operation {operation_id} with params: {all_params}")
    
    try:
        gis = _establish_secure_connection(portal_url, username, password, token)

        if item_type and item_type.lower().strip() == 'feature layer':
            logger.info("Guardrail HIT: Correcting item_type from 'Feature Layer' to 'Feature Service'")
            item_type = 'Feature Service'
        
        query_components = []
        
        if exact_name: query_components.append(f'title:"{exact_name.strip()}"')
        if query:
            fuzzy_query = f'(title:{query}*^3 OR tags:{query}*^2 OR description:{query}* OR snippet:{query}*)'
            query_components.append(fuzzy_query)
        if item_type: query_components.append(f'type:"{item_type}"')
        if owner:
            if owner.lower() == 'me' and gis.users.me: query_components.append(f'owner:{gis.users.me.username}')
            elif owner.lower() in ['org', 'organization']: query_components.append(f'orgid:{gis.properties.id}')
            elif owner.lower() == 'public': query_components.append('access:public')
            else: query_components.append(f'owner:"{owner}"')
        if tags:
            tag_list = tags if isinstance(tags, list) else [t.strip() for t in tags.split(',')]
            formatted_tags = [f'"{tag}"' for tag in tag_list]
            query_components.append(f'tags:({" AND ".join(formatted_tags)})')
        if created_after:
            try:
                date_obj = datetime.strptime(created_after, '%Y-%m-%d')
                timestamp_ms = int(date_obj.timestamp() * 1000)
                query_components.append(f'created:[{timestamp_ms} TO *]')
            except ValueError:
                logger.warning(f"Invalid date format for created_after: {created_after}")
        
        final_query = ' AND '.join(query_components) if query_components else '*'
        logger.info(f"Executing search with query: {final_query}")
        
        sort_field = sort_by
        sort_order = "desc" if sort_by in ["modified", "created", "relevance", "numViews", "avgRating"] else "asc"
        
        results = gis.content.search(query=final_query, max_items=min(max_results, 100), sort_field=sort_field, sort_order=sort_order, outside_org=not organization_filter)
        
        formatted_results = []
        for item in results[:7]:
            summary = "No description available."
            if item.description:
                soup = BeautifulSoup(item.description, "html.parser")
                clean_text = ' '.join(soup.get_text().split())
                summary = (clean_text[:250] + '...') if len(clean_text) > 250 else clean_text

            formatted_results.append({
                "title": item.title,
                "type": item.type,
                "owner": item.owner,
                "summary": summary,
                "tags": item.tags[:5] if item.tags else [],
                "url": item.homepage
            })
        
        response = {"status": "success", "operation_id": operation_id, "data": formatted_results}
        return json.dumps(response, default=str)
        
    except Exception as e:
        logger.error(f"Search operation failed: {e}", exc_info=True)
        return json.dumps({"status": "error", "error": {"type": type(e).__name__, "message": str(e)}})

def _establish_secure_connection(portal_url, username, password, token) -> GIS:
    try:
        portal_url = os.getenv('ARCGIS_URL')
        username = username or os.getenv('ARCGIS_USERNAME')
        password = password or os.getenv('ARCGIS_PASSWORD')
        if token: gis = GIS(portal_url, token=token)
        elif username and password: gis = GIS(portal_url, username, password)
        elif portal_url: gis = GIS(portal_url)
        else: gis = GIS()
        logger.info(f"Established portal connection: {gis.url}")
        return gis
    except Exception as e:
        logger.error(f"Failed to establish portal connection: {str(e)}")
        raise ConnectionError(f"Portal connection failed: {str(e)}")

def run_server():
    logger.info("[SUCCESS] ArcGIS Server is ready")
    return mcp

if __name__ == "__main__":
    # This is the corrected block for running the server with FastMCP.
    mcp_instance = run_server()
    mcp_instance.run()
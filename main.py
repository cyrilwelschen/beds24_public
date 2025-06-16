#!/usr/bin/env python3
"""
Streamlit web app for Beds24 booking reports with proper error handling
"""

import streamlit as st
import os
import json
import requests
import pathlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
from io import BytesIO
import base64
from dataclasses import dataclass
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
import hashlib

HASHED_PASSWORD = "5fb7707068606a228442f989a5c051614ac62c539c26d81e335e91c0a11a94eb"

def verify_password(password: str) -> bool:
    """Verify if the provided password matches the stored hash"""
    hashed_input = hashlib.sha256(password.encode()).hexdigest()
    return hashed_input == HASHED_PASSWORD

@dataclass
class Reservation:
    """Data class for reservation information"""
    booking_id: str
    guest_name: str
    room_name: str
    room_id: str
    unit_id: str
    checkin: str
    checkout: str
    status: str
    adults: int
    children: int
    nights: int
    notes: str
    
    def __post_init__(self):
        """Convert date strings to datetime objects for comparison"""
        self.checkin_date = datetime.strptime(self.checkin, '%Y-%m-%d').date()
        self.checkout_date = datetime.strptime(self.checkout, '%Y-%m-%d').date()

# Your existing TokenStorage and Beds24APIClient classes with modifications
class TokenStorage:
    """Handles storage and retrieval of Beds24 API tokens"""
    
    def __init__(self, storage_dir: str = None):
        """
        Initialize token storage
        
        Args:
            storage_dir: Directory to store tokens (defaults to current directory)
        """
        if storage_dir is None:
            self.storage_dir = os.path.abspath(".")  # Use current directory
        else:
            self.storage_dir = storage_dir
        
        # Create storage directory if it doesn't exist
        pathlib.Path(self.storage_dir).mkdir(parents=True, exist_ok=True)
        
        self.token_file = os.path.join(self.storage_dir, "tokens.json")
        self.tokens = self._load_tokens()
        print("\nToken Storage Status:")
        print(f"Storage directory: {self.storage_dir}")
        print(f"Token file: {self.token_file}")
        print("Current tokens:")
        print(json.dumps(self.tokens, indent=2))
    
    def _load_tokens(self) -> Dict:
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                st.warning(f"Error loading stored tokens: {e}")
                return {}
        return {}
    
    def _save_tokens(self):
        try:
            with open(self.token_file, 'w') as f:
                json.dump(self.tokens, f, indent=2)
        except Exception as e:
            st.error(f"Error saving tokens: {e}")
    
    def store_tokens(self, access_token: str, refresh_token: Optional[str] = None, 
                    token_type: str = "refresh", expires_at: Optional[datetime] = None):
        self.tokens = {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'token_type': token_type,
            'expires_at': expires_at.isoformat() if expires_at else None,
            'last_updated': datetime.now().isoformat()
        }
        self._save_tokens()
    
    def get_tokens(self) -> Optional[Dict]:
        if not self.tokens:
            return None
            
        if self.tokens.get('expires_at'):
            expires_at = datetime.fromisoformat(self.tokens['expires_at'])
            if datetime.now() >= expires_at:
                if self.tokens.get('refresh_token'):
                    return self.tokens
                return None
                
        return self.tokens
    
    def clear_tokens(self):
        self.tokens = {}
        if os.path.exists(self.token_file):
            try:
                os.remove(self.token_file)
            except Exception as e:
                st.warning(f"Error removing token file: {e}")

class Beds24APIClient:
    def __init__(self):
        self.base_url = "https://beds24.com/api/v2"
        self.session = requests.Session()
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_storage = TokenStorage()
        
    def authenticate(self, long_life_token=None, refresh_token=None, invite_code=None) -> tuple[bool, str]:
        """Authenticate with Beds24 API using available credentials"""
        try:
            # Try to get stored tokens first
            stored_tokens = self.token_storage.get_tokens()
            if stored_tokens:
                self.access_token = stored_tokens['access_token']
                self.refresh_token = stored_tokens.get('refresh_token')
                
                # If token is expired but we have a refresh token, try to refresh
                if stored_tokens.get('expires_at'):
                    expires_at = datetime.fromisoformat(stored_tokens['expires_at'])
                    if datetime.now() >= expires_at and self.refresh_token:
                        try:
                            self.authenticate_with_refresh_token(self.refresh_token)
                            return True, "Successfully refreshed authentication"
                        except requests.exceptions.RequestException as e:
                            return False, f"Failed to refresh token: {str(e)}"
                else:
                    return True, "Using stored authentication"
                
                if self.access_token:
                    return True, "Using stored authentication"
            
            # Try provided credentials or environment variables
            long_life_token = long_life_token or os.getenv('BEDS24_LONG_LIFE_TOKEN')
            refresh_token = refresh_token or os.getenv('BEDS24_REFRESH_TOKEN')
            invite_code = invite_code or os.getenv('BEDS24_INVITE_CODE')
            
            if long_life_token:
                self.access_token = long_life_token
                self.token_storage.store_tokens(
                    access_token=self.access_token,
                    token_type="long_life"
                )
                return True, "Authenticated with long-life token"
            elif refresh_token:
                self.authenticate_with_refresh_token(refresh_token)
                return True, "Authenticated with refresh token"
            elif invite_code:
                self.authenticate_with_invite_code(invite_code)
                return True, "Authenticated with invite code"
            else:
                return False, "No authentication credentials provided"
                
        except requests.exceptions.RequestException as e:
            return False, f"Authentication failed: {str(e)}"
        except Exception as e:
            return False, f"Unexpected error during authentication: {str(e)}"
    
    def authenticate_with_invite_code(self, invite_code: str) -> Dict:
        """Exchange invite code for tokens"""
        url = f"{self.base_url}/authentication/setup"
        headers = {'accept': 'application/json', 'code': invite_code}
        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        token_data = response.json()
        
        self.access_token = token_data.get('token')
        self.refresh_token = token_data.get('refreshToken')
        
        # Store tokens for future use
        expires_at = datetime.now() + timedelta(seconds=token_data.get('expiresIn', 0))
        self.token_storage.store_tokens(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            token_type="refresh",
            expires_at=expires_at
        )
        
        return token_data
    
    def authenticate_with_refresh_token(self, refresh_token: str) -> Dict:
        """Generate new access token using refresh token"""
        url = f"{self.base_url}/authentication/token"
        headers = {'accept': 'application/json', 'refreshToken': refresh_token}
        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        token_data = response.json()
        
        self.access_token = token_data.get('token')
        self.refresh_token = refresh_token  # Keep the original refresh token
        
        # Store tokens for future use
        expires_at = datetime.now() + timedelta(seconds=token_data.get('expiresIn', 0))
        self.token_storage.store_tokens(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            token_type="refresh",
            expires_at=expires_at
        )
        
        return token_data
    
    def get_bookings(self, date_filter: Optional[str] = None, 
                     arrival_from: Optional[str] = None,
                     arrival_to: Optional[str] = None,
                     departure_from: Optional[str] = None,
                     departure_to: Optional[str] = None) -> tuple[list, str]:
        if not self.access_token:
            return [], "No access token available. Please authenticate first."
        url = f"{self.base_url}/bookings"
        headers = {
            'accept': 'application/json',
            'token': self.access_token
        }
        params = {
            'includeInvoiceItems': 'false',
            'includeInfoItems': 'true'
        }
        if date_filter:
            params['filter'] = date_filter
        if arrival_from:
            params['arrivalFrom'] = arrival_from
        if arrival_to:
            params['arrivalTo'] = arrival_to
        if departure_from:
            params['departureFrom'] = departure_from
        if departure_to:
            params['departureTo'] = departure_to
        try:
            response = self.session.get(url, headers=headers, params=params)
            if response.status_code == 401:
                return [], "Authentication failed. Please check your credentials."
            elif response.status_code == 403:
                return [], "Access forbidden. Please check your API permissions."
            elif response.status_code == 404:
                return [], "API endpoint not found. Please check the API URL."
            elif response.status_code == 429:
                return [], "Rate limit exceeded. Please try again later."
            response.raise_for_status()
            bookings_data = response.json()
            return bookings_data.get('data', []), "Success"
        except Exception as e:
            return [], f"Error: {e}"

def parse_booking_data(booking: Dict) -> Reservation:
    """Convert booking data to Reservation object"""
    # Room lookup dictionary
    room_lookup = {
        (564321, 1): "101",
        (564321, 2): "201",
        (564321, 3): "301",
        (564321, 4): "302",
        (564327, 1): "102",
        (564327, 2): "202",
        (564325, 1): "103",
        (564325, 2): "203",
        (564328, 1): "104",
        (564328, 2): "204",
        (564326, 1): "105",
        (564326, 2): "106",
        (564326, 3): "205",
        (564326, 4): "206",
        (564322, 1): "404",
        (564322, 2): "504",
        (564322, 3): "604",
        (564323, 1): "401",
        (564323, 2): "402",
        (564323, 3): "403",
        (564323, 4): "501",
        (564323, 5): "502",
        (564323, 6): "503",
        (564323, 7): "601",
        (564323, 8): "602",
        (564323, 9): "603",
        (564324, 1): "405",
        (564324, 2): "505",
        (564324, 3): "605",
        (570543, 1): "701",
        (570545, 1): "702",
        (570542, 1): "703",
        (570544, 1): "704",
        (570546, 1): "705",
    }
    
    try:
        room_id = int(booking.get('roomId', 0))
        unit_id = int(booking.get('unitId', 0))
    except Exception:
        room_id = booking.get('roomId', 0)
        unit_id = booking.get('unitId', 0)
    
    lookup_key = (room_id, unit_id)
    room_name = room_lookup.get(lookup_key, f"Room {room_id}-{unit_id}")
    
    return Reservation(
        booking_id=str(booking.get('id', '')),
        guest_name=f"{booking.get('lastName', '')} {booking.get('firstName', '')}".strip(),
        room_name=room_name,
        room_id=str(room_id),
        unit_id=str(unit_id),
        checkin=booking.get('arrival', ''),
        checkout=booking.get('departure', ''),
        status=booking.get('status', ''),
        adults=booking.get('numAdult', 0),
        children=booking.get('numChild', 0),
        nights=(datetime.strptime(booking.get('departure', ''), '%Y-%m-%d') - 
               datetime.strptime(booking.get('arrival', ''), '%Y-%m-%d')).days,
        notes=booking.get('notes', '')
    )

def categorize_reservations(reservations: List[Reservation], 
                          target_date: datetime.date) -> Tuple[List[Reservation], List[Reservation], List[Reservation]]:
    """Categorize reservations by arrival, departure, and stay-through"""
    arrivals = []
    departures = []
    stay_through = []
    
    for reservation in reservations:
        # Skip reservations with status "black"
        if reservation.status == "black":
            continue
            
        if reservation.checkin_date == target_date:
            arrivals.append(reservation)
        if reservation.checkout_date == target_date:
            departures.append(reservation)
        if reservation.checkin_date < target_date < reservation.checkout_date:
            stay_through.append(reservation)
    
    # Sort each list by room_name
    arrivals = sorted(arrivals, key=lambda r: r.room_name)
    departures = sorted(departures, key=lambda r: r.room_name)
    stay_through = sorted(stay_through, key=lambda r: r.room_name)
    
    return arrivals, departures, stay_through

def create_reservation_table(reservations: List[Reservation]) -> Table:
    """Create a table for a list of reservations"""
    if not reservations:
        return Table([['No reservations for this category']], 
                    colWidths=[8.5*inch])
    
    # Table headers
    headers = ['Room', 'Guest Name', 'Guests', 'Nights', 'Check-In', 'Check-Out', 'Notes']
    data = [headers]
    
    # Add reservation data
    for reservation in reservations:
        guests_str = f"{reservation.adults}A"
        if reservation.children > 0:
            guests_str += f"+{reservation.children}C"
            
        # Format check-in/out dates as 'Tue 17.05'
        checkin_fmt = ''
        checkout_fmt = ''
        try:
            checkin_dt = datetime.strptime(reservation.checkin, '%Y-%m-%d')
            checkin_fmt = checkin_dt.strftime('%a %d.%m')
        except Exception:
            checkin_fmt = reservation.checkin
        try:
            checkout_dt = datetime.strptime(reservation.checkout, '%Y-%m-%d')
            checkout_fmt = checkout_dt.strftime('%a %d.%m')
        except Exception:
            checkout_fmt = reservation.checkout
            
        # Use Paragraph for fields that may be long
        wrap_style = ParagraphStyle('wrap', fontName='Helvetica', fontSize=10, leading=12, wordWrap='CJK')
        row = [
            reservation.room_name,
            Paragraph(reservation.guest_name or "No Name", wrap_style),
            guests_str,
            str(reservation.nights),
            checkin_fmt,
            checkout_fmt,
            Paragraph(reservation.notes or "", wrap_style)
        ]
        data.append(row)
    
    # Create table with wider and more balanced column widths
    table = Table(data, colWidths=[
        0.5*inch,  # Room
        1.6*inch,  # Guest Name
        0.5*inch,  # Guests
        0.5*inch,  # Nights
        0.8*inch,  # Check-In
        0.8*inch,  # Check-Out
        3*inch   # Notes (wider)
    ])
    
    # Apply table style
    table.setStyle(TableStyle([
        # Header style
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
        
        # Data style
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        
        # Grid
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        
        # Left align guest name and notes
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('ALIGN', (6, 0), (6, -1), 'LEFT'),
        # Reduce padding
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    
    return table

def create_pdf_report(reservations: List[Reservation], target_date: datetime.date) -> BytesIO:
    """Create a PDF report from reservation data"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, 
                              topMargin=0.2*inch,
                              bottomMargin=0.2*inch,
                              leftMargin=0.2*inch,
                              rightMargin=0.2*inch)
        
        # Get styles
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=20,
            spaceAfter=20,
            textColor=colors.black,
            alignment=1  # Center alignment
        )
        
        section_style = ParagraphStyle(
            'SectionHeader',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=12,
            textColor=colors.black,
            alignment=0  # Left align
        )
        
        # Story elements
        story = []
        
        # Title
        title = f"Welschen Report - {target_date.strftime('%B %d, %Y')}"
        story.append(Paragraph(title, title_style))
        
        # Categorize reservations
        arrivals, departures, stay_through = categorize_reservations(reservations, target_date)
        
        # Calculate guest numbers for each section
        def guest_str(reslist):
            total_a = sum(r.adults for r in reslist)
            total_c = sum(r.children for r in reslist)
            if total_c:
                return f"{total_a}A +{total_c}C"
            else:
                return f"{total_a}A"
        
        # Arrivals section
        story.append(Paragraph(f"ARRIVALS - {guest_str(arrivals)}", section_style))
        story.append(Spacer(1, 5))
        story.append(create_reservation_table(arrivals))
        story.append(Spacer(1, 10))
        
        # Departures section
        story.append(Paragraph(f"DEPARTURES - {guest_str(departures)}", section_style))
        story.append(Spacer(1, 5))
        story.append(create_reservation_table(departures))
        story.append(Spacer(1, 10))
        
        # Stay Through section
        story.append(Paragraph(f"STAYING THROUGH - {guest_str(stay_through)}", section_style))
        story.append(Spacer(1, 5))
        story.append(create_reservation_table(stay_through))
        story.append(Spacer(1, 15))
        
        # Footer
        footer_text = f"Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} via Beds24 API v2"
        story.append(Paragraph(footer_text, styles['Normal']))
        
        # Build PDF
        doc.build(story)
        buffer.seek(0)
        return buffer
        
    except ImportError as e:
        st.error(f"PDF generation requires reportlab package. Error: {e}")
        return None
    except Exception as e:
        st.error(f"Error creating PDF: {e}")
        return None

def create_cleaning_report(reservations: List[Reservation], target_date: datetime.date) -> BytesIO:
    """Create a PDF cleaning report from reservation data"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, 
                              topMargin=0.2*inch,
                              bottomMargin=0.2*inch,
                              leftMargin=0.2*inch,
                              rightMargin=0.2*inch)
        
        # Get styles
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=20,
            spaceAfter=20,
            textColor=colors.black,
            alignment=1  # Center alignment
        )
        
        section_style = ParagraphStyle(
            'SectionHeader',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=12,
            textColor=colors.black,
            borderWidth=0,
            borderPadding=5,
            spaceBefore=30,
            alignment=1  # Center alignment
        )
        
        # Story elements
        story = []
        
        # Title
        title = f"Welschen Cleaning Report - {target_date.strftime('%B %d, %Y')}"
        story.append(Paragraph(title, title_style))
        
        # Categorize reservations
        arrivals, departures, stay_through = categorize_reservations(reservations, target_date)
        
        # Calculate guest numbers for each section
        def guest_str(reslist):
            total_a = sum(r.adults for r in reslist)
            total_c = sum(r.children for r in reslist)
            if total_c:
                return f"{total_a}A +{total_c}C"
            else:
                return f"{total_a}A"
        
        # Add spacer for better spacing after title
        story.append(Spacer(1, 20))
        
        # Arrivals section
        story.append(Paragraph(f"ARRIVALS - {guest_str(arrivals)}", section_style))
        story.append(Spacer(1, 5))
        story.append(create_cleaning_table(arrivals))
        story.append(Spacer(1, 10))
        
        # Departures section
        story.append(Paragraph(f"DEPARTURES - {guest_str(departures)}", section_style))
        story.append(Spacer(1, 5))
        story.append(create_cleaning_table(departures))
        story.append(Spacer(1, 10))
        
        # Stay Through section
        story.append(Paragraph(f"STAYING THROUGH - {guest_str(stay_through)}", section_style))
        story.append(Spacer(1, 5))
        story.append(create_cleaning_table(stay_through))
        story.append(Spacer(1, 15))
        
        # Footer
        footer_text = f"Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} via Beds24 API v2"
        story.append(Paragraph(footer_text, styles['Normal']))
        
        # Build PDF
        doc.build(story)
        buffer.seek(0)
        return buffer
        
    except ImportError as e:
        st.error(f"PDF generation requires reportlab package. Error: {e}")
        return None
    except Exception as e:
        st.error(f"Error creating PDF: {e}")
        return None

def create_cleaning_table(reservations: List[Reservation]) -> Table:
    """Create a table for a list of reservations without guest and notes columns"""
    if not reservations:
        return Table([['No reservations for this category']], 
                    colWidths=[8.5*inch])
    
    # Table headers
    headers = ['Room', 'Guests', 'Nights', 'Check-In', 'Check-Out']
    data = [headers]
    
    # Add reservation data
    for reservation in reservations:
        guests_str = f"{reservation.adults}A"
        if reservation.children > 0:
            guests_str += f"+{reservation.children}C"
            
        # Format check-in/out dates as 'Tue 17.05'
        checkin_fmt = ''
        checkout_fmt = ''
        try:
            checkin_dt = datetime.strptime(reservation.checkin, '%Y-%m-%d')
            checkin_fmt = checkin_dt.strftime('%a %d.%m')
        except Exception:
            checkin_fmt = reservation.checkin
        try:
            checkout_dt = datetime.strptime(reservation.checkout, '%Y-%m-%d')
            checkout_fmt = checkout_dt.strftime('%a %d.%m')
        except Exception:
            checkout_fmt = reservation.checkout
            
        row = [
            reservation.room_name,
            guests_str,
            str(reservation.nights),
            checkin_fmt,
            checkout_fmt
        ]
        data.append(row)
    
    # Create table with wider and more balanced column widths
    table = Table(data, colWidths=[
        1.5*inch,  # Room
        1.0*inch,  # Guests
        0.8*inch,  # Nights
        1.2*inch,  # Check-In
        1.2*inch   # Check-Out
    ])
    
    # Apply table style
    table.setStyle(TableStyle([
        # Header style
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
        
        # Data style
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        
        # Grid
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        
        # Reduce padding
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    
    # Wrap the table in a container to center it
    container = Table([[table]], colWidths=[5.7*inch])
    container.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    
    return container

def fetch_all_relevant_bookings(client, target_date: datetime.date) -> list:
    """
    Fetch arrivals, departures, and stay-through bookings for the target date, and deduplicate them.
    Mirrors the logic from beds24_reservation_report.py.
    """
    start_date = target_date - timedelta(days=30)
    end_date = target_date + timedelta(days=30)

    # 1. Arrivals for the target date
    arrivals, _ = client.get_bookings(
        date_filter='arrivals',
        arrival_from=target_date.strftime('%Y-%m-%d'),
        arrival_to=target_date.strftime('%Y-%m-%d')
    )
    # 2. Departures for the target date
    departures, _ = client.get_bookings(
        date_filter='departures',
        departure_from=target_date.strftime('%Y-%m-%d'),
        departure_to=target_date.strftime('%Y-%m-%d')
    )
    # 3. Stay-through bookings
    stay_through, _ = client.get_bookings(
        arrival_from=start_date.strftime('%Y-%m-%d'),
        arrival_to=target_date.strftime('%Y-%m-%d'),
        departure_from=target_date.strftime('%Y-%m-%d'),
        departure_to=end_date.strftime('%Y-%m-%d')
    )
    # Combine and deduplicate
    booking_ids = set()
    combined = []
    for booking in arrivals + departures + stay_through:
        booking_id = booking.get('id')
        if booking_id not in booking_ids:
            booking_ids.add(booking_id)
            combined.append(booking)
    return combined

# Streamlit App
def main():
    st.set_page_config(page_title="Beds24 Booking Reports", page_icon="🏨")
    
    st.title("WELSCHEN Daily Reports")
    st.write("Generate and download booking reports from your Beds24 account")
    
    # Sidebar for authentication
    st.sidebar.header("Authentication")
    
    auth_method = st.sidebar.selectbox(
        "Choose authentication method:",
        ["Environment Variables", "Long Life Token", "Refresh Token", "Invite Code"]
    )
    
    # Authentication inputs
    long_life_token = None
    refresh_token = None
    invite_code = None
    
    if auth_method == "Long Life Token":
        long_life_token = st.sidebar.text_input("Long Life Token", type="password")
    elif auth_method == "Refresh Token":
        refresh_token = st.sidebar.text_input("Refresh Token", type="password")
    elif auth_method == "Invite Code":
        invite_code = st.sidebar.text_input("Invite Code", type="password")
    
    # Clear stored tokens button
    if st.sidebar.button("Clear Stored Tokens"):
        client = Beds24APIClient()
        client.token_storage.clear_tokens()
        st.sidebar.success("Stored tokens cleared")
    
    # Main interface
    st.header("Generate Report")
    
    # Date selection
    target_date = st.date_input("Select arrival date:", datetime.now())
    
    # Password protection
    password = st.text_input("Enter password to generate reports:", type="password")
    
    if st.button("Generate Booking Report", type="primary", disabled=not verify_password(password)):
        if not verify_password(password):
            st.error("❌ Incorrect password. Please try again.")
            return
            
        # Initialize client
        client = Beds24APIClient()
        
        # Show loading spinner
        with st.spinner("Authenticating..."):
            auth_success, auth_message = client.authenticate(
                long_life_token=long_life_token,
                refresh_token=refresh_token,
                invite_code=invite_code
            )
        
        if not auth_success:
            st.error(f"❌ Authentication failed: {auth_message}")
            st.info("💡 Make sure you have set up your credentials correctly.")
            return
        
        st.success(f"✅ {auth_message}")
        
        # Fetch bookings
        with st.spinner(f"Fetching bookings for {target_date}..."):
            bookings = fetch_all_relevant_bookings(client, target_date)
        
        if not bookings:
            st.error("❌ No bookings found for the selected date.")
            return
        
        st.success(f"✅ Found {len(bookings)} bookings for {target_date}")
        
        if bookings:
            # Convert bookings to Reservation objects
            reservations = [parse_booking_data(booking) for booking in bookings]
            
            # Generate PDF
            with st.spinner("Generating PDF..."):
                pdf_buffer = create_pdf_report(reservations, target_date)
            
            if pdf_buffer:
                st.download_button(
                    label="Download Reception Report",
                    data=pdf_buffer,
                    file_name=f"beds24_reservations_{target_date}.pdf",
                    mime="application/pdf"
                )
                
                # Generate and add cleaning report download button
                cleaning_pdf_buffer = create_cleaning_report(reservations, target_date)
                if cleaning_pdf_buffer:
                    st.download_button(
                        label="Download Housekeeping Report",
                        data=cleaning_pdf_buffer,
                        file_name=f"welschen_cleaning_{target_date}.pdf",
                        mime="application/pdf"
                    )
            
            # Display summary
            arrivals, departures, stay_through = categorize_reservations(reservations, target_date)
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Arrivals", len(arrivals))
            with col2:
                st.metric("Departures", len(departures))
            with col3:
                st.metric("Staying Through", len(stay_through))

if __name__ == "__main__":
    main()
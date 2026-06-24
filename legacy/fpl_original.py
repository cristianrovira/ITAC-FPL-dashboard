import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime  # Import datetime for time handling
import matplotlib.pyplot as plt

#st.set_page_config(layout="wide")

st.title("FPL Dashboard Analysis")

# --- Step 1: Number of Accounts ---
st.sidebar.header("Account Information")
num_accounts = st.sidebar.number_input("Number of Accounts", min_value=1, step=1, value=1)

# --- Step 2: File Upload ---
uploaded_files = []
for i in range(num_accounts):
    st.sidebar.subheader(f"Upload Files for Account {i + 1}")
    uploaded_files.append(st.sidebar.file_uploader(f"Upload 12 Monthly Files for Account {i + 1}", accept_multiple_files=True))

# --- Step 3: Working Hours ---
#st.header("Manufacturing Working Hours")

# Operating Days
saturday_op = st.checkbox("Saturday an operating day")
sunday_op = st.checkbox("Sunday an operating day")

# Operating Shifts with dynamic time selection
st.subheader("Define Working Shifts")

shifts = []

# Debug: Ensure shifts are captured
def log_shifts(shifts):
    st.sidebar.write("### Shifts Configured:")
    for idx, shift in enumerate(shifts):
        st.sidebar.write(f"Shift {idx + 1}: {shift[0]} to {shift[1]} ({shift[2]})")

# 1st Shift (Monday - Friday)
col1, col2, col3 = st.columns([1, 2, 2])
with col1:
    shift_1 = st.checkbox("1st Shift - Monday - Friday")
with col2:
    shift_1_start = st.time_input("Start time (1st Shift - Monday - Friday)", value=pd.Timestamp("06:30").time(), key="shift_1_start")
with col3:
    shift_1_end = st.time_input("End time (1st Shift - Monday - Friday)", value=pd.Timestamp("15:00").time(), key="shift_1_end")
if shift_1:
    shifts.append((shift_1_start, shift_1_end, "Weekday"))

# 2nd Shift (Monday - Friday)
col1, col2, col3 = st.columns([1, 2, 2])
with col1:
    shift_2 = st.checkbox("2nd Shift - Monday - Friday")
with col2:
    shift_2_start = st.time_input("Start time (2nd Shift - Monday - Friday)", value=pd.Timestamp("15:00").time(), key="shift_2_start")
with col3:
    shift_2_end = st.time_input("End time (2nd Shift - Monday - Friday)", value=pd.Timestamp("23:00").time(), key="shift_2_end")
if shift_2:
    shifts.append((shift_2_start, shift_2_end, "Weekday"))

# 3rd Shift (Monday - Friday)
col1, col2, col3 = st.columns([1, 2, 2])
with col1:
    shift_3 = st.checkbox("3rd Shift - Monday - Friday")
with col2:
    shift_3_start = st.time_input("Start time (3rd Shift - Monday - Friday)", value=pd.Timestamp("23:00").time(), key="shift_3_start")
with col3:
    shift_3_end = st.time_input("End time (3rd Shift - Monday - Friday)", value=pd.Timestamp("06:30").time(), key="shift_3_end")
if shift_3:
    shifts.append((shift_3_start, shift_3_end, "Weekday"))

# Special weekend shifts
if saturday_op:
    st.subheader("Saturday Shifts")
    col1, col2, col3 = st.columns([1, 2, 2])
    with col1:
        saturday_shift_start = st.time_input("Start time (Saturday Shift)", value=pd.Timestamp("08:00").time(), key="saturday_start")
    with col3:
        saturday_shift_end = st.time_input("End time (Saturday Shift)", value=pd.Timestamp("16:00").time(), key="saturday_end")
    shifts.append((saturday_shift_start, saturday_shift_end, "Saturday"))

if sunday_op:
    st.subheader("Sunday Shifts")
    col1, col2, col3 = st.columns([1, 2, 2])
    with col1:
        sunday_shift_start = st.time_input("Start time (Sunday Shift)", value=pd.Timestamp("08:00").time(), key="sunday_start")
    with col3:
        sunday_shift_end = st.time_input("End time (Sunday Shift)", value=pd.Timestamp("16:00").time(), key="sunday_end")
    shifts.append((sunday_shift_start, sunday_shift_end, "Sunday"))

log_shifts(shifts)  # Log configured shifts

# --- Step 4: Number of Demand Columns ---
num_demand_columns = st.number_input("Number of Demand Columns", min_value=1, step=1, value=1)
demand_columns = [st.text_input(f"Enter name of Demand Column {i + 1}") for i in range(num_demand_columns)]

# --- Step 5: Interval ---
interval = st.radio("Select Data Interval", [1, 0.5, 0.25], index=0)

# --- Step 7: File Preprocessing ---
def preprocess_file(file, demand_column_name):
    # Read the file, skipping the first 3 rows
    df = pd.read_excel(file, skiprows=3)

    # Parse DateTime or Date and Time columns
    if "DateTime" in df.columns:
        df["DateTime"] = pd.to_datetime(df["DateTime"], errors="coerce")
        df["Time"] = df["DateTime"].dt.time
        df["Hour"] = df["DateTime"].dt.hour
    elif "Date" in df.columns and "Time" in df.columns:
        # Parse time strings into datetime.time objects
        df["Time"] = df["Time"].apply(lambda x: datetime.strptime(x, "%I:%M %p").time() if isinstance(x, str) else x)
        df["DateTime"] = pd.to_datetime(df["Date"] + " " + df["Time"].astype(str), errors="coerce")
        df["Hour"] = df["DateTime"].dt.hour

    # Validate the demand column
    if demand_column_name not in df.columns:
        st.error(f"Demand column '{demand_column_name}' not found!")
        return None

    # Ensure demand values are numeric
    df["Demand"] = pd.to_numeric(df[demand_column_name], errors="coerce")

    # Add additional datetime-related columns
    df["DayName"] = df["DateTime"].dt.day_name()
    df["Month"] = df["DateTime"].dt.strftime("%B")
    df["Year"] = df["DateTime"].dt.year

    return df



def calculate_operating(row, shifts, saturday_op=False, sunday_op=False):
    """
    Determine if the given row is within operating hours.
    """
    if pd.isna(row["Time"]):
        return False

    # Exclude weekends unless marked as operating
    if row["DayName"] == "Saturday" and not saturday_op:
        return False
    if row["DayName"] == "Sunday" and not sunday_op:
        return False

    # Check against shifts
    for start, end, day_type in shifts:
        if day_type == "Weekday" and row["DayName"] in ["Saturday", "Sunday"]:
            continue
        if day_type != "Weekday" and row["DayName"] != day_type:
            continue

        # Handle shifts crossing midnight
        row_time = row["Time"]
        if start < end:
            if start <= row_time <= end:
                return True
        else:
            if row_time >= start or row_time <= end:
                return True
    return False


def calculate_on_peak(row):
    """
    Determine if a row is within on-peak hours.
    """
    if row["DayName"] in ["Saturday", "Sunday"]:
        return False  # Weekends are off-peak

    hour = row["Hour"]
    if 4 <= row["DateTime"].month <= 10:  # April to October
        return 12 <= hour <= 21  # On-peak: 12 PM to 9 PM
    else:  # November to March
        return (6 <= hour <= 10) or (18 <= hour <= 22)  # On-peak: 6-10 AM, 6-10 PM


def generate_monthly_summary(data, shifts, interval, saturday_op=False, sunday_op=False):
    """
    Generate a monthly summary table with operating, non-operating, on-peak, and off-peak demands.
    """
    data = data.reset_index(drop=True)

    # Calculate if the time falls within operating hours
    data["Operating"] = data.apply(lambda row: calculate_operating(row, shifts, saturday_op, sunday_op), axis=1)
    data["OnPeak"] = data.apply(calculate_on_peak, axis=1)

    # Scale demand values by the interval
    data["Demand"] *= interval

    # Monthly aggregation with logical masks
    monthly_summary = data.groupby("Month").agg(
        NotOperating=("Demand", lambda x: x[~data.loc[x.index, "Operating"]].sum()),
        OperatingShift=("Demand", lambda x: x[data.loc[x.index, "Operating"]].sum()),
        TotalDemand=("Demand", "sum"),
        OnPeakOperating=("Demand", lambda x: x[data.loc[x.index, "Operating"] & data.loc[x.index, "OnPeak"]].sum()),
        OffPeakOperating=("Demand", lambda x: x[data.loc[x.index, "Operating"] & ~data.loc[x.index, "OnPeak"]].sum()),
        OnPeakNotOperating=("Demand", lambda x: x[~data.loc[x.index, "Operating"] & data.loc[x.index, "OnPeak"]].sum()),
        OffPeakNotOperating=("Demand", lambda x: x[~data.loc[x.index, "Operating"] & ~data.loc[x.index, "OnPeak"]].sum()),
    ).reset_index()
    
    # Add Ratio column
    monthly_summary["NOratio"] = monthly_summary["NotOperating"] / monthly_summary["TotalDemand"]

    # Add a total row
    total_row = monthly_summary.sum(numeric_only=True).to_frame().T
    total_row["Month"] = "Total"
    total_row["NOratio"] = total_row["NotOperating"] / total_row["TotalDemand"]
    monthly_summary = pd.concat([monthly_summary, total_row], ignore_index=True)
    #st.write("Debug: Operating and OnPeak Flags for January")
    #st.dataframe(combined_data[combined_data["Month"] == "January"])

    # Add a total row
    total_row = monthly_summary.sum(numeric_only=True).to_frame().T
    total_row["Month"] = "Total"
    #monthly_summary = pd.concat([monthly_summary, total_row], ignore_index=True)

    return monthly_summary



if st.button("Process Files"):
    all_data = []
    for files in uploaded_files:
        for file in files:
            for demand_column in demand_columns:
                df = preprocess_file(file, demand_column)
                if df is not None:
                    all_data.append(df)

    if all_data:
        combined_data = pd.concat(all_data, ignore_index=True)
        combined_data["Operating"] = combined_data.apply(
             lambda row: calculate_operating(row, shifts, saturday_op, sunday_op), axis=1)

        combined_data["OnPeak"] = combined_data.apply(calculate_on_peak, axis=1)

        # Generate the summary table
        summary = generate_monthly_summary(combined_data, shifts, interval, saturday_op, sunday_op)

        # Add a numerical month column for sorting
        month_order = {
            "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
            "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12
        }
        
        combined_data["MonthOrder"] = combined_data["Month"].map(month_order)

        # Sort combined data by Year and MonthOrder
        combined_data = combined_data.sort_values(by=["Year", "MonthOrder", "DateTime"])

        # Add "Month / Year" column to combined_data
        combined_data["Month / Year"] = combined_data["Month"] + " / " + combined_data["Year"].astype(str)

        
        
        summary["MonthOrder"] = summary["Month"].map(month_order)

        # Separate the "Total" row from the rest of the data
        total_row = summary[summary["Month"] == "Total"]
        summary = summary[summary["Month"] != "Total"]

        # Add the Year column from combined_data
        year_mapping = combined_data.groupby("Month")["Year"].first().reset_index()
        year_mapping["MonthOrder"] = year_mapping["Month"].map(month_order)
        summary = summary.merge(year_mapping, on=["MonthOrder", "Month"], how="left")

        # Sort by Year and MonthOrder
        summary = summary.sort_values(by=["Year", "MonthOrder"])

        # Create a "Month / Year" column
        summary["Month / Year"] = summary["Month"] + " / " + summary["Year"].astype(str)

        # Re-add the "Total" row at the end
        total_row["Month / Year"] = "Total / Total"  # Add placeholder value for "Total" row
        summary = pd.concat([summary, total_row], ignore_index=True)

        # Rearrange columns to include "Month / Year" first
        summary = summary[["Month / Year"] + [col for col in summary.columns if col not in ["Month / Year", "MonthOrder", "Year"]]]

        # Display the Monthly Summary table
        st.write("Monthly Summary")
        summary.index = summary.index + 1
        st.dataframe(summary)

        # Calculate maximum and average demand for each month
        demand_stats = combined_data.groupby(["Month", "Year"]).agg(
            MaxDemand=("Demand", "max"),
            AvgDemand=("Demand", "mean")
        ).reset_index()

        # Add a numerical representation for months to sort them correctly
        month_order = {
            "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
            "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12
        }
        demand_stats["MonthOrder"] = demand_stats["Month"].map(month_order)

        # Sort the DataFrame by Year and then by MonthOrder
        demand_stats = demand_stats.sort_values(by=["Year", "MonthOrder"]).reset_index(drop=True)

        # Create a "Month / Year" column for the demand stats table
        demand_stats["Month / Year"] = demand_stats["Month"] + " / " + demand_stats["Year"].astype(str)

        # Reorder columns
        demand_stats = demand_stats[["Month / Year", "MaxDemand", "AvgDemand"]]

        # Display the Maximum and Average Demand Table
        st.write("### Maximum and Average Demand Table")
        st.dataframe(demand_stats)

        # Plot Maximum and Average Demand
        fig, ax = plt.subplots(figsize=(12, 6))
        bar_width = 0.4
        x = np.arange(len(demand_stats["Month / Year"]))

        # Bars for Max Demand
        ax.bar(x - bar_width / 2, demand_stats["MaxDemand"], bar_width, label="Max Demand (kW)", color="orange")

        # Bars for Average Demand
        ax.bar(x + bar_width / 2, demand_stats["AvgDemand"], bar_width, label="Avg Demand (kW)", color="blue")

        # Customizing the chart
        ax.set_title("Maximum and Average Demand by Month", fontsize=16)
        ax.set_xlabel("Month / Year", fontsize=12)
        ax.set_ylabel("Demand (kW)", fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels(demand_stats["Month / Year"], rotation=45)
        ax.legend()

        # Display the plot in Streamlit
        st.pyplot(fig)

        # Exclude the "Total" row for plotting
        plot_data = summary[summary["Month / Year"] != "Total / Total"]

        # Plot Operating Shift and Not Operating
        fig, ax = plt.subplots(figsize=(12, 6))
        bar_width = 0.4
        x = np.arange(len(plot_data["Month / Year"]))

        # Bars for Operating Shift
        ax.bar(x - bar_width / 2, plot_data["OperatingShift"], bar_width, label="Operating Shift", color="green")

        # Bars for Not Operating
        ax.bar(x + bar_width / 2, plot_data["NotOperating"], bar_width, label="Not Operating", color="red")

        # Customizing the chart
        ax.set_title("Operating Shift vs Not Operating by Month", fontsize=16)
        ax.set_xlabel("Month / Year", fontsize=12)
        ax.set_ylabel("Demand (kWh)", fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels(plot_data["Month / Year"], rotation=45)
        ax.legend()

        # Show the plot in Streamlit
        st.pyplot(fig)


        # Plot OnPeakOperating and OffPeakOperating
        fig1, ax1 = plt.subplots(figsize=(12, 6))
        bar_width = 0.35  # Adjust bar width for two categories
        x = np.arange(len(plot_data["Month / Year"]))

        # Bars for OnPeakOperating
        ax1.bar(x - bar_width / 2, plot_data["OnPeakOperating"], bar_width, label="OnPeak Operating", color="green")

        # Bars for OffPeakOperating
        ax1.bar(x + bar_width / 2, plot_data["OffPeakOperating"], bar_width, label="OffPeak Operating", color="blue")

        # Customizing the chart
        ax1.set_title("OnPeak and OffPeak Operating Demand", fontsize=16)
        ax1.set_xlabel("Month / Year", fontsize=12)
        ax1.set_ylabel("Demand (kW)", fontsize=12)
        ax1.set_xticks(x)
        ax1.set_xticklabels(plot_data["Month / Year"], rotation=45)
        ax1.legend()

        # Show the plot in Streamlit
        st.pyplot(fig1)


        # Plot OnPeakNotOperating and OffPeakNotOperating
        fig2, ax2 = plt.subplots(figsize=(12, 6))
        bar_width = 0.35  # Adjust bar width for two categories
        x = np.arange(len(plot_data["Month / Year"]))

        # Bars for OnPeakNotOperating
        ax2.bar(x - bar_width / 2, plot_data["OnPeakNotOperating"], bar_width, label="OnPeak Not Operating", color="orange")

        # Bars for OffPeakNotOperating
        ax2.bar(x + bar_width / 2, plot_data["OffPeakNotOperating"], bar_width, label="OffPeak Not Operating", color="red")

        # Customizing the chart
        ax2.set_title("OnPeak and OffPeak Not Operating Demand", fontsize=16)
        ax2.set_xlabel("Month / Year", fontsize=12)
        ax2.set_ylabel("Demand (kW)", fontsize=12)
        ax2.set_xticks(x)
        ax2.set_xticklabels(plot_data["Month / Year"], rotation=45)
        ax2.legend()

        # Show the plot in Streamlit
        st.pyplot(fig2)




# Exclude the "Total" row for plotting
        plot_data = summary[summary["Month / Year"] != "Total / Total"]

        # Plot Total Demand for each Month
        fig, ax = plt.subplots(figsize=(12, 6))

        ax.bar(plot_data["Month / Year"], plot_data["TotalDemand"], color="blue", label="Total Demand (kW)")

        # Customize the chart
        ax.set_title("Total Demand (kW) by Month", fontsize=16)
        ax.set_xlabel("Month / Year", fontsize=12)
        ax.set_ylabel("Total Demand (kW)", fontsize=12)
        plt.xticks(rotation=45)
        ax.legend()

        # Show the plot in Streamlit
        st.pyplot(fig)
        
         # Separate Demand Plots for Each Month
        st.write("### Monthly Demand (kW) Details")
        unique_months = combined_data["Month / Year"].unique()

        for month_year in unique_months:
            monthly_data = combined_data[combined_data["Month / Year"] == month_year]
            fig, ax = plt.subplots(figsize=(12,6))

            ax.plot(
                monthly_data["DateTime"],
                monthly_data["Demand"],
                #marker="o",
                linestyle="-",
                color="green",
                label=f"Demand (kW) - {month_year}"
            )

            ax.set_title(f"Demand (kW) for {month_year}", fontsize=16)
            ax.set_xlabel("Time", fontsize=12)
            ax.set_ylabel("Demand (kW)", fontsize=12)
            ax.grid(True)
            plt.xticks(rotation=45)
            ax.legend()
            st.pyplot(fig)
            
         # --- Weekly and Tuesday Plots ---
        st.write("### Weekly and Daily Demand Analysis")
        unique_months = combined_data["Month / Year"].unique()

        for month_year in unique_months:
            # Filter data for the current month
            monthly_data = combined_data[combined_data["Month / Year"] == month_year]

            # Choose a random week (Monday to Sunday)
            random_week_start = monthly_data.loc[monthly_data["DayName"] == "Monday"].iloc[0]["DateTime"]
            random_week_end = random_week_start + pd.Timedelta(days=6)

            weekly_data = monthly_data[(monthly_data["DateTime"] >= random_week_start) &
                                       (monthly_data["DateTime"] <= random_week_end)]

            # Filter for Tuesday of the same week
            tuesday_data = weekly_data[weekly_data["DayName"] == "Tuesday"]

            # --- Weekly and Tuesday Plots ---
        st.write("### Weekly and Daily Demand Analysis")
        unique_months = combined_data["Month / Year"].unique()

        for month_year in unique_months:
            # Filter data for the current month
            monthly_data = combined_data[combined_data["Month / Year"] == month_year]

            # Choose a random week (Monday to Sunday)
            random_week_start = monthly_data.loc[monthly_data["DayName"] == "Monday"].iloc[0]["DateTime"]
            random_week_end = random_week_start + pd.Timedelta(days=6)

            weekly_data = monthly_data[(monthly_data["DateTime"] >= random_week_start) &
                                       (monthly_data["DateTime"] <= random_week_end)]

            # Filter for Tuesday of the same week
            tuesday_data = weekly_data[weekly_data["DayName"] == "Tuesday"]

            # Plot Weekly Demand
            if not weekly_data.empty:
                avg_demand = weekly_data["Demand"].mean()
                fig, ax = plt.subplots(figsize=(12, 6))
                ax.plot(
                    weekly_data["DateTime"],
                    weekly_data["Demand"],
                    label="Weekly Demand",
                    color="blue",
                    #marker="o",
                    linestyle="-"
                )
                ax.axhline(y=avg_demand, color="red", linestyle="--", label=f"Average Demand ({avg_demand:.2f} kW)")
                ax.set_title(f"Demand (kW) for Week of {random_week_start.date()} ({month_year})", fontsize=16)
                ax.set_xlabel("Date and Time", fontsize=12)
                ax.set_ylabel("Demand (kW)", fontsize=12)
                ax.grid(True)
                plt.xticks(rotation=45)
                ax.legend()
                st.pyplot(fig)

            # Plot Tuesday Demand
            if not tuesday_data.empty:
                fig, ax = plt.subplots(figsize=(12, 6))
                ax.plot(
                    tuesday_data["DateTime"],
                    tuesday_data["Demand"],
                    label="Tuesday Demand",
                    color="green",
                    #marker="o",
                    linestyle="-"
                )

                # Highlight Operating and Non-Operating Times
                operating_data = tuesday_data[tuesday_data["Operating"]]
                non_operating_data = tuesday_data[~tuesday_data["Operating"]]

                if not operating_data.empty:
                    ax.scatter(
                        operating_data["DateTime"],
                        operating_data["Demand"],
                        color="blue",
                        label="Operating Time",
                        #marker="o"
                    )
                if not non_operating_data.empty:
                    ax.scatter(
                        non_operating_data["DateTime"],
                        non_operating_data["Demand"],
                        color="red",
                        label="Not Operating Time",
                        #marker="x"
                    )

                ax.set_title(f"Tuesday Demand (kW) for Week of {random_week_start.date()} ({month_year})", fontsize=16)
                ax.set_xlabel("Date and Time", fontsize=12)
                ax.set_ylabel("Demand (kW)", fontsize=12)
                ax.grid(True)
                plt.xticks(rotation=45)
                ax.legend()
                st.pyplot(fig)

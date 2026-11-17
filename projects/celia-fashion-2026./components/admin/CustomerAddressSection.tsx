"use client"

import * as React from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import AsteriskIcon from "@/components/icons/asterisk.svg"
import USFlagIcon from "@/components/icons/us-flag.svg"

interface CustomerAddressSectionProps {
  data: {
    country: string
    region: string
    apartments: string
    city: string
    zipCode: string
    phoneNumber: string
  }
  onUpdate: (data: any) => void
  onNext: () => void
  onSaveAsDraft: () => void
}

export function CustomerAddressSection({ data, onUpdate, onNext, onSaveAsDraft }: CustomerAddressSectionProps) {
  const handleInputChange = (field: string, value: any) => {
    onUpdate({ [field]: value })
  }

  return (
    <div className="pt-6 flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-3 h-6 rounded bg-[#a9e195]" />
        <h2 className="text-[24px] font-bold leading-[28px] tracking-[-0.24px] text-[#3f434a]">
          Address
        </h2>
      </div>

      {/* Form Fields */}
      <div className="flex flex-col gap-6">
        {/* Country and Region Row */}
        <div className="flex gap-6">
          <div className="flex-1 flex flex-col gap-2">
            <div className="flex items-center gap-1">
              <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
                Country
              </Label>
              <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
            </div>
            <Select value={data.country} onValueChange={(value: string) => handleInputChange("country", value)}>
              <SelectTrigger>
                <SelectValue placeholder="Select a country" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="us">United States</SelectItem>
                <SelectItem value="uk">United Kingdom</SelectItem>
                <SelectItem value="ca">Canada</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex-1 flex flex-col gap-2">
            <div className="flex items-center gap-1">
              <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
                Region
              </Label>
              <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
            </div>
            <Select value={data.region} onValueChange={(value: string) => handleInputChange("region", value)}>
              <SelectTrigger>
                <SelectValue placeholder="Select your region" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="northeast">Northeast</SelectItem>
                <SelectItem value="midwest">Midwest</SelectItem>
                <SelectItem value="south">South</SelectItem>
                <SelectItem value="west">West</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Apartments Field */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-1">
            <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
              Apartments, suite, etc
            </Label>
            <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
          </div>
          <Input
            value={data.apartments}
            onChange={(e) => handleInputChange("apartments", e.target.value)}
            placeholder="Enter your apartments, suite, etc"
          />
        </div>

        {/* City and Zip Code Row */}
        <div className="flex gap-6">
          <div className="flex-1 flex flex-col gap-2">
            <div className="flex items-center gap-1">
              <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
                City
              </Label>
              <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
            </div>
            <Input
              value={data.city}
              onChange={(e) => handleInputChange("city", e.target.value)}
              placeholder="Enter your City"
            />
          </div>

          <div className="flex-1 flex flex-col gap-2">
            <div className="flex items-center gap-1">
              <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
                Zip Code
              </Label>
              <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
            </div>
            <Input
              value={data.zipCode}
              onChange={(e) => handleInputChange("zipCode", e.target.value)}
              placeholder="Enter zip code"
            />
          </div>
        </div>

        {/* Phone Number Field */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-1">
            <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
              Phone Number
            </Label>
            <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
          </div>
          <div className="flex h-10 items-center rounded-lg border border-[#0b12341a] bg-white shadow-[0px_0px_0px_1px_rgba(11,18,52,0.10),0px_1px_2px_rgba(11,18,52,0.15)]">
            <div className="flex items-center gap-2 px-3 border-r border-[#0b12341a]">
              <USFlagIcon className="w-5 h-5 rounded-full" />
              <span className="text-[14px] text-[#0b1234]">+1</span>
              <svg className="w-3 h-3 text-[#696f8c]" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
              </svg>
            </div>
            <input
              type="tel"
              value={data.phoneNumber}
              onChange={(e) => handleInputChange("phoneNumber", e.target.value)}
              placeholder="555‑0199"
              className="flex-1 px-3 text-[14px] text-[#696f8c] bg-transparent outline-none"
            />
          </div>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex items-center justify-between pt-4">
        <button
          onClick={onSaveAsDraft}
          className="text-[16px] font-medium tracking-[0.16px] text-[#0b1234] hover:underline"
        >
          Save as Draft
        </button>
        <button
          onClick={onNext}
          className="px-6 py-2 rounded-lg bg-[#0f62fe] text-[16px] font-medium tracking-[0.16px] text-white hover:bg-[#0f62fe]/90 transition-colors"
        >
          Save and Next
        </button>
      </div>
    </div>
  )
}
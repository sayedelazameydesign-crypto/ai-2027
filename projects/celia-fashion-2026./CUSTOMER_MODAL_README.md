# Customer Modal - Setup & Usage

## What Was Created

A complete multi-step customer modal with three sections matching the Figma designs:

### Components Created:
1. **AddCustomerModal.tsx** - Main modal wrapper with tab navigation
2. **CustomerOverviewSection.tsx** - First step: Avatar, name, language, phone, consents
3. **CustomerAddressSection.tsx** - Second step: Country, region, address, city, zip, phone
4. **CustomerNotesSection.tsx** - Third step: Customer notes textarea

### UI Components Added:
- `components/ui/dialog.tsx` - Radix Dialog wrapper
- `components/ui/select.tsx` - Radix Select dropdown
- `components/ui/checkbox.tsx` - Radix Checkbox
- `components/ui/input.tsx` - Styled input field
- `components/ui/textarea.tsx` - Styled textarea
- `components/ui/label.tsx` - Radix Label

### Icons Copied:
- `components/icons/asterisk.svg` - Required field marker
- `components/icons/chevron-down.svg` - Dropdown arrow
- `components/icons/avatar-placeholder.svg` - Avatar icon
- `components/icons/edit.svg` - Edit icon
- `components/icons/us-flag.svg` - US flag for phone input
- `components/icons/checkbox-empty.svg` - Checkbox icon

### Demo Page:
- `app/(admin)/dashboard/customers/page.tsx` - Demo page with "Add New Customer" button

## How to Run

1. **Navigate to the project directory:**
   ```powershell
   cd fashion-store
   ```

2. **Start the development server:**
   ```powershell
   npm run dev
   ```

3. **Open your browser:**
   ```
   http://localhost:3000/dashboard/customers
   ```

4. **Click "Add New Customer"** to see the modal in action

## Features

- ✅ **Multi-step form** with 3 sections (Overview → Address → Notes)
- ✅ **Tab navigation** - Click tabs to switch between sections
- ✅ **Progress flow** - "Save and Next" buttons move forward
- ✅ **Form state management** - Data persists across steps
- ✅ **Radix UI components** - Accessible dialog, select, checkbox
- ✅ **Exact design match** - Colors, fonts (Inter), shadows, spacing
- ✅ **Required field indicators** - Green asterisk icons
- ✅ **Phone input with country code** - US flag + dropdown
- ✅ **Consent checkboxes** - Marketing emails and SMS
- ✅ **Colored section tags** - Purple, Green, Coral
- ✅ **TypeScript** - Fully typed components

## Modal Flow

1. **Overview Tab**: Fill in customer basic info
   - Upload avatar
   - First/Last name
   - Language selection
   - Phone number
   - Marketing consents

2. **Address Tab**: Enter address details
   - Country/Region dropdowns
   - Apartments/suite
   - City/Zip code
   - Phone number

3. **Notes Tab**: Add customer notes
   - Text area for notes
   - Edit icon indicator

## Design Details Matched

- **Colors:**
  - Active tab: #0f62fe (blue)
  - Gray text: #696f8c
  - Dark text: #364050, #3f434a, #0b1234
  - Required asterisk: #17a584 (green)
  - Tags: #cabdff (purple), #a9e195 (green), #ff9d8f (coral)

- **Typography:**
  - Font: Inter (400, 500, 700 weights)
  - Heading: 24px/700
  - Buttons: 16px/500
  - Labels: 14px/500
  - Body: 14px/400
  - Small: 12px/400

- **Layout:**
  - Modal width: 600px
  - Border radius: 16px (container), 8px (inputs), 4px (tags)
  - Shadow: Multi-layer elevation
  - Spacing: 24px (sections), 8px (fields), 12px (headers)

## Troubleshooting

If you see TypeScript errors:
```powershell
npm install
```

If SVG icons don't load:
- Check that `@svgr/webpack` is installed
- Verify `global.d.ts` exists with SVG module declaration
- Check `next.config.ts` has turbopack SVG rules

## Next Steps

The modal is ready to integrate with your backend:
1. Connect form submission to your API
2. Add form validation
3. Handle file upload for avatar
4. Store customer data in database
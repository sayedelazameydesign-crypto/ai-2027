import { Mail, Phone, MapPin, Instagram, Facebook } from "lucide-react";
import Link from "next/link";

export default function ContactPage() {
    return (
        <div className="bg-white min-h-screen pt-24 pb-12">
            <div className="max-w-4xl mx-auto px-6 space-y-12">
                <div className="text-center space-y-4">
                    <h1 className="text-4xl font-heading font-bold text-zinc-900">Contact Us</h1>
                    <p className="text-zinc-500">We'd love to hear from you. Reach out for inquiries, support, or just to say hi.</p>
                </div>

                <div className="grid md:grid-cols-2 gap-12">
                    {/* Contact Info */}
                    <div className="space-y-8">
                        <div className="flex items-start gap-4">
                            <div className="p-3 bg-pink-50 rounded-lg text-[var(--secondary)]">
                                <Phone className="w-6 h-6" />
                            </div>
                            <div>
                                <h3 className="font-bold text-lg">Phone / WhatsApp</h3>
                                <p className="text-zinc-600">01126212452</p>
                                <p className="text-sm text-zinc-400">Daily: 10 AM - 10 PM</p>
                            </div>
                        </div>

                        <div className="flex items-start gap-4">
                            <div className="p-3 bg-pink-50 rounded-lg text-[var(--secondary)]">
                                <Mail className="w-6 h-6" />
                            </div>
                            <div>
                                <h3 className="font-bold text-lg">Email</h3>
                                <a href="mailto:info@celiafashion.com" className="text-zinc-600 hover:text-[var(--secondary)]">info@celiafashion.com</a>
                            </div>
                        </div>

                        <div className="flex items-start gap-4">
                            <div className="p-3 bg-pink-50 rounded-lg text-[var(--secondary)]">
                                <MapPin className="w-6 h-6" />
                            </div>
                            <div>
                                <h3 className="font-bold text-lg">Headquarters</h3>
                                <p className="text-zinc-600">Cairo, Egypt.</p>
                            </div>
                        </div>

                        <div className="pt-6 border-t border-zinc-100">
                            <h3 className="font-bold text-lg mb-4">Follow Us</h3>
                            <div className="flex gap-4">
                                <Link href="#" className="p-2 bg-zinc-100 rounded-full hover:bg-[var(--primary)] hover:text-white transition-colors"><Instagram className="w-5 h-5" /></Link>
                                <Link href="#" className="p-2 bg-zinc-100 rounded-full hover:bg-[var(--primary)] hover:text-white transition-colors"><Facebook className="w-5 h-5" /></Link>
                            </div>
                        </div>
                    </div>

                    {/* Form */}
                    <div className="bg-zinc-50 p-8 rounded-2xl shadow-sm">
                        <form className="space-y-6">
                            <div>
                                <label className="block text-sm font-bold text-zinc-700 mb-2">Name</label>
                                <input type="text" className="w-full px-4 py-3 bg-white border border-zinc-200 rounded focus:outline-none focus:border-[var(--secondary)]" placeholder="Your Name" />
                            </div>
                            <div>
                                <label className="block text-sm font-bold text-zinc-700 mb-2">Email</label>
                                <input type="email" className="w-full px-4 py-3 bg-white border border-zinc-200 rounded focus:outline-none focus:border-[var(--secondary)]" placeholder="you@example.com" />
                            </div>
                            <div>
                                <label className="block text-sm font-bold text-zinc-700 mb-2">Message</label>
                                <textarea rows={4} className="w-full px-4 py-3 bg-white border border-zinc-200 rounded focus:outline-none focus:border-[var(--secondary)]" placeholder="How can we help?" />
                            </div>
                            <button className="w-full py-3 bg-black text-white font-bold rounded hover:bg-[var(--secondary)] transition-colors">
                                Send Message
                            </button>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    );
}
